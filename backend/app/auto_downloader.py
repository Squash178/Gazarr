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
    poll_interval: float = 900.0
    max_results_per_magazine: int = 1


@dataclass
class IssueState:
    active: bool = False
    links: Set[str] = field(default_factory=set)


class AutoDownloader:
    """Periodically searches providers and enqueues new issues automatically."""

    def __init__(self, config: AutoDownloadConfig) -> None:
        self.config = config
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="gazarr-auto-downloader")
        logger.info(
            "Started auto downloader: poll=%ss max_results=%s",
            self.config.poll_interval,
            self.config.max_results_per_magazine,
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

    async def _sync_once(self) -> None:
        with Session(engine) as session:
            connection: Optional[SabnzbdConnection] = get_sabnzbd_connection(session)
            if not is_configured(connection):
                raise SabnzbdNotConfigured("SABnzbd connection missing.")

            results = await search_magazines(session)
            if not results:
                logger.debug("Auto downloader scan produced no search results.")
                return

            job_index = self._build_job_index(session)
            thresholds = self._load_thresholds(session)
            candidates = self._select_candidates(results, job_index, thresholds)
            if not candidates:
                logger.debug("Auto downloader found no candidates to enqueue.")
                return

            for issue_key, result in candidates:
                if self._stop_event.is_set():
                    break
                try:
                    await self._enqueue_result(session, connection, result)
                except (SabnzbdError, SabnzbdNotConfigured) as exc:
                    logger.warning("Auto download failed to enqueue %s from %s: %s", result.title, result.provider, exc)
                    continue
                except Exception:
                    logger.exception("Unexpected failure enqueuing %s from %s", result.title, result.provider)
                    continue
                self._mark_issue_active(job_index, issue_key, str(result.link))

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
        thresholds: Dict[str, Tuple[Optional[int], Optional[int]]],
    ) -> List[Tuple[str, SearchResult]]:
        selections: List[Tuple[str, SearchResult]] = []
        per_magazine_count: Dict[str, int] = {}
        for result in results:
            magazine = (result.magazine_title or "").strip()
            if not magazine:
                continue
            magazine_key = magazine.lower()
            limit = thresholds.get(magazine_key)
            if limit and not self._passes_threshold(result, limit):
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
            count = per_magazine_count.get(magazine_key, 0)
            if count >= max(1, self.config.max_results_per_magazine):
                continue
            per_magazine_count[magazine_key] = count + 1
            selections.append((issue_key, result))
        return selections

    def _mark_issue_active(self, job_index: Dict[str, IssueState], issue_key: str, link: Optional[str]) -> None:
        bucket = job_index.setdefault(issue_key, IssueState())
        bucket.active = True
        if link:
            bucket.links.add(link)

    def _load_thresholds(self, session: Session) -> Dict[str, Tuple[Optional[int], Optional[int]]]:
        limits: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
        statement = select(Magazine.title, Magazine.auto_download_since_year, Magazine.auto_download_since_issue)
        for title, since_year, since_issue in session.exec(statement):
            if since_year is None and since_issue is None:
                continue
            key = title.strip().lower()
            limits[key] = (since_year, since_issue)
        return limits

    @staticmethod
    def _passes_threshold(result: SearchResult, threshold: Tuple[Optional[int], Optional[int]]) -> bool:
        min_year, min_issue = threshold
        if min_year is None and min_issue is None:
            return True
        issue_year = result.issue_year
        issue_number = result.issue_number
        if min_year is not None:
            if issue_year is None:
                return False
            if issue_year < min_year:
                return False
            if issue_year > min_year:
                return True
            # same year
            if min_issue is None:
                return True
            if issue_number is None:
                return False
            return issue_number > min_issue
        # Only issue threshold specified
        if issue_number is None:
            return False
        return issue_number > min_issue


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
