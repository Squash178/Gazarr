import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlmodel import Session, select

from .database import engine
from .models import DownloadJob, Magazine
from .sabnzbd import SabnzbdConnection, SabnzbdError, SabnzbdNotConfigured, enqueue_url, is_configured
from .schemas import SearchResult
from .services import get_sabnzbd_connection, search_magazines, upsert_download_job

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = {"pending", "queued", "downloading", "processing", "completed", "moved"}


@dataclass(frozen=True)
class AutoDownloadConfig:
    poll_interval: float = 43200.0
    max_results_per_scan: int = 1

    @property
    def poll_interval_hours(self) -> float:
        return self.poll_interval / 3600.0


@dataclass
class IssueState:
    active: bool = False
    links: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class MagazineGuard:
    min_year: Optional[int]
    min_issue: Optional[int]
    tokens: Set[str]


class AutoDownloader:
    """Periodically searches providers and enqueues new issues automatically."""

    def __init__(self, config: AutoDownloadConfig) -> None:
        self.config = config
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._scan_lock = asyncio.Lock()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="gazarr-auto-downloader")
        logger.info(
            "Started auto downloader: poll=%ss (~%.2fh) max_results_per_scan=%s",
            self.config.poll_interval,
            self.config.poll_interval_hours,
            self.config.max_results_per_scan,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Stopped auto downloader.")

    def update_config(self, config: AutoDownloadConfig) -> None:
        self.config = config

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._sync_once()
                except asyncio.CancelledError:
                    raise
                except SabnzbdNotConfigured:
                    logger.debug("Skipping auto download cycle: SABnzbd is not configured.")
                except Exception:
                    logger.exception("Auto downloader failed during cycle.")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.poll_interval)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def scan_now(self) -> Tuple[bool, int]:
        if not self._task or self._task.done():
            self.start()
        if self._scan_lock.locked():
            logger.info("Auto download scan already running; ignoring manual trigger.")
            return False, 0
        enqueued = await self._sync_once()
        return True, enqueued

    async def _sync_once(self) -> int:
        async with self._scan_lock:
            with Session(engine) as session:
                connection: Optional[SabnzbdConnection] = get_sabnzbd_connection(session)
                if not is_configured(connection):
                    raise SabnzbdNotConfigured("SABnzbd connection missing.")

                results = await search_magazines(session)
                if not results:
                    logger.debug("Auto downloader scan produced no search results.")
                    return 0

                job_index = self._build_job_index(session)
                guards = self._load_guards(session)
                candidates = self._select_candidates(results, job_index, guards)
                if not candidates:
                    logger.debug("Auto downloader found no candidates to enqueue.")
                    return 0

                enqueued = 0
                for issue_key, result in candidates:
                    if self._stop_event.is_set():
                        break
                    try:
                        await self._enqueue_result(session, connection, result)
                        enqueued += 1
                    except (SabnzbdError, SabnzbdNotConfigured) as exc:
                        logger.warning(
                            "Auto download failed to enqueue %s from %s: %s", result.title, result.provider, exc
                        )
                        continue
                    except Exception:
                        logger.exception("Unexpected failure enqueuing %s from %s", result.title, result.provider)
                        continue
                    self._mark_issue_active(job_index, issue_key, str(result.link))
                return enqueued

    async def _enqueue_result(self, session: Session, connection: SabnzbdConnection, result: SearchResult) -> None:
        sab_result = await enqueue_url(str(result.link), title=result.title, connection=connection)
        metadata = {
            "magazine_title": result.magazine_title,
            "issue_code": result.issue_code,
            "issue_label": result.issue_label,
            "issue_year": result.issue_year,
            "issue_month": result.issue_month,
            "issue_number": result.issue_number,
        }
        for nzo_id in sab_result.nzo_ids:
            upsert_download_job(
                session,
                nzo_id=nzo_id,
                title=result.title,
                magazine_title=result.magazine_title,
                link=str(result.link),
                status="queued",
                issue_code=metadata["issue_code"],
                issue_label=metadata["issue_label"],
                issue_year=metadata["issue_year"],
                issue_month=metadata["issue_month"],
                issue_number=metadata["issue_number"],
            )
        logger.info(
            "Auto downloaded %s from %s (issue_code=%s)",
            result.title,
            result.provider,
            result.issue_code,
        )

    def _build_job_index(self, session: Session) -> Dict[str, IssueState]:
        index: Dict[str, IssueState] = {}
        statement = select(DownloadJob).where(DownloadJob.magazine_title != None)  # noqa: E711
        for job in session.exec(statement):
            issue_key = _issue_identifier(
                job.magazine_title,
                job.issue_code,
                job.issue_year,
                job.issue_month,
                job.issue_number,
                job.title,
            )
            if not issue_key:
                continue
            bucket = index.setdefault(issue_key, IssueState())
            status_value = (job.status or "").lower()
            if status_value in ACTIVE_JOB_STATUSES:
                bucket.active = True
            if job.link:
                bucket.links.add(job.link)
        return index

    def _select_candidates(
        self,
        results: Iterable[SearchResult],
        job_index: Dict[str, IssueState],
        guards: Dict[str, MagazineGuard],
    ) -> List[Tuple[str, SearchResult]]:
        selections: List[Tuple[str, SearchResult]] = []
        max_per_scan = max(1, self.config.max_results_per_scan)
        for result in results:
            if len(selections) >= max_per_scan:
                break
            magazine = (result.magazine_title or "").strip()
            if not magazine:
                continue
            magazine_key = magazine.lower()
            guard = guards.get(magazine_key)
            if guard and not self._passes_guard(result, guard):
                continue
            issue_key = _issue_identifier(
                result.magazine_title,
                result.issue_code,
                result.issue_year,
                result.issue_month,
                result.issue_number,
                result.title,
            )
            if not issue_key:
                continue
            bucket = job_index.get(issue_key)
            if bucket:
                if bucket.active:
                    continue
                link_value = str(result.link)
                if link_value in bucket.links:
                    continue
            selections.append((issue_key, result))
        return selections

    def _mark_issue_active(self, job_index: Dict[str, IssueState], issue_key: str, link: Optional[str]) -> None:
        bucket = job_index.setdefault(issue_key, IssueState())
        bucket.active = True
        if link:
            bucket.links.add(link)

    def _load_guards(self, session: Session) -> Dict[str, MagazineGuard]:
        guards: Dict[str, MagazineGuard] = {}
        statement = select(
            Magazine.title,
            Magazine.auto_download_since_year,
            Magazine.auto_download_since_issue,
        )
        for title, since_year, since_issue in session.exec(statement):
            if not title:
                continue
            key = title.strip().lower()
            tokens = _extract_title_tokens(title)
            guards[key] = MagazineGuard(min_year=since_year, min_issue=since_issue, tokens=tokens)
        return guards

    @staticmethod
    def _passes_guard(result: SearchResult, guard: MagazineGuard) -> bool:
        if guard.min_year is None and guard.min_issue is None and not guard.tokens:
            return True
        issue_year = result.issue_year
        issue_number = result.issue_number
        if guard.min_year is not None:
            if issue_year is None:
                return False
            if issue_year < guard.min_year:
                return False
            if issue_year > guard.min_year:
                return True
            # same year
            if guard.min_issue is None:
                return True
            if issue_number is None:
                return False
            if issue_number < guard.min_issue:
                return False
        elif guard.min_issue is not None:
            if issue_number is None or issue_number < guard.min_issue:
                return False

        if guard.tokens:
            normalized_title_tokens = set(_normalize_text(result.title or "").split())
            if not normalized_title_tokens:
                return False
            for token in guard.tokens:
                if token not in normalized_title_tokens:
                    return False
        # Only issue threshold specified
        return True


def _issue_identifier(
    magazine_title: Optional[str],
    issue_code: Optional[str],
    issue_year: Optional[int],
    issue_month: Optional[int],
    issue_number: Optional[int],
    fallback_title: Optional[str],
) -> Optional[str]:
    if not magazine_title:
        return None
    prefix = magazine_title.strip().lower()
    if issue_code:
        return f"{prefix}::code::{issue_code}"
    parts: List[str] = []
    if issue_year is not None:
        parts.append(f"Y{issue_year:04d}")
    if issue_month is not None:
        parts.append(f"M{issue_month:02d}")
    if issue_number is not None:
        parts.append(f"N{issue_number:04d}")
    if parts:
        return f"{prefix}::{'-'.join(parts)}"
    if fallback_title:
        return f"{prefix}::title::{fallback_title.strip().lower()}"
    return None


STOP_WORDS = {"mag", "magazine", "ebook", "digital", "issue", "revista"}


def _extract_title_tokens(title: str) -> Set[str]:
    normalized = _normalize_text(title)
    tokens = {token for token in normalized.split() if len(token) >= 3 and token not in STOP_WORDS}
    return tokens


def _normalize_text(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in value)
    return " ".join(cleaned.split())
