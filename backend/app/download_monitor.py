import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional

from sqlmodel import Session

from .database import engine
from .download_processor import process_download_entry
from .models import DownloadJob
from .services import find_download_job_for_entry, mark_download_job_moved

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorConfig:
    source_dir: Path
    target_dir: Path
    staging_dir: Optional[Path] = None
    poll_interval: float = 10.0
    settle_seconds: float = 30.0
    cover_dir: Optional[Path] = None


@dataclass(frozen=True)
class DownloadEntry:
    name: str
    path: Path
    type: Literal["file", "directory"]
    size: int
    modified_ts: float
    ready: bool


class DownloadMonitor:
    """Polls a downloads folder and moves completed items into the library."""

    def __init__(self, config: MonitorConfig) -> None:
        self.config = config
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        if config.cover_dir:
            self.cover_dir = config.cover_dir.expanduser()
        else:
            self.cover_dir = (config.target_dir / "covers")

        if not self.config.source_dir:
            raise ValueError("MonitorConfig.source_dir is required.")
        if not self.config.target_dir:
            raise ValueError("MonitorConfig.target_dir is required.")

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="gazarr-download-monitor")
        logger.info(
            "Started download monitor: source=%s target=%s poll=%ss settle=%ss",
            self.config.source_dir,
            self.config.target_dir,
            self.config.poll_interval,
            self.config.settle_seconds,
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
            logger.info("Stopped download monitor.")

    async def _run(self) -> None:
        try:
            await self._ensure_directories()
            while not self._stop_event.is_set():
                try:
                    await self._scan_once()
                except Exception:
                    logger.exception("Download monitor scan failed.")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.poll_interval)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Download monitor terminated unexpectedly.")
            raise

    async def _ensure_directories(self) -> None:
        for path in (self.config.source_dir, self.config.target_dir, self.config.staging_dir, self.cover_dir):
            if path and not path.exists():
                path.mkdir(parents=True, exist_ok=True)

    async def _scan_once(self) -> None:
        source = self.config.source_dir
        try:
            entries = list(source.iterdir())
        except FileNotFoundError:
            logger.warning("Download source directory missing: %s", source)
            await self._ensure_directories()
            return

        for entry in entries:
            if self._stop_event.is_set():
                break
            if entry.name.startswith("."):
                continue
            if not entry.exists():
                continue
            if not _entry_is_ready(entry, self.config.settle_seconds):
                continue
            self._move_entry(entry)

    def _move_entry(self, entry: Path) -> None:
        with Session(engine) as session:
            job = find_download_job_for_entry(session, entry.name)
            if not job:
                logger.debug("Skipping %s: no matching Gazarr download job.", entry)
                return
            if job.status not in {"completed", "processing"}:
                logger.debug(
                    "Skipping %s: job %s not ready for import (status=%s).",
                    entry,
                    job.id,
                    job.status,
                )
                return
            job_id = job.id
        if job_id is None:
            logger.warning("Skipping %s: matched job missing identifier.", entry)
            return

        staging_dir = self.config.staging_dir
        target_dir = self.config.target_dir
        if staging_dir:
            staging_destination = staging_dir / entry.name
            staging_destination = self._resolve_destination(staging_destination)
            try:
                shutil.move(str(entry), str(staging_destination))
                final_destination = process_download_entry(
                    staging_destination,
                    target_dir,
                    resolver=self._resolve_destination,
                    job_id=job_id,
                    cover_dir=self.cover_dir,
                )
                self._record_move(job_id, final_destination)
                logger.info("Processed download: %s -> %s", staging_destination, final_destination)
            except Exception:
                logger.exception("Failed to process %s in staging", entry)
        else:
            try:
                final_destination = process_download_entry(
                    entry,
                    target_dir,
                    resolver=self._resolve_destination,
                    job_id=job_id,
                    cover_dir=self.cover_dir,
                )
                self._record_move(job_id, final_destination)
                logger.info("Processed download: %s -> %s", entry, final_destination)
            except Exception:
                logger.exception("Failed to process %s", entry)

    def _resolve_destination(self, destination: Path) -> Path:
        if not destination.exists():
            return destination
        base = destination.stem if destination.is_file() else destination.name
        suffix = destination.suffix if destination.is_file() else ""
        parent = destination.parent
        counter = 1
        while True:
            candidate = parent / f"{base}-{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _record_move(self, job_id: int, destination: Path) -> None:
        try:
            with Session(engine) as session:
                job = session.get(DownloadJob, job_id)
                if not job:
                    logger.warning("Unable to record move for job id %s; job missing.", job_id)
                    return
                mark_download_job_moved(session, job, destination)
        except Exception:
            logger.exception("Failed to update download job for %s", job_id)


def _entry_is_ready(path: Path, settle_seconds: float) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    age = time.time() - stat.st_mtime
    if age < settle_seconds:
        return False
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                child_stat = child.stat()
            except FileNotFoundError:
                return False
            child_age = time.time() - child_stat.st_mtime
            if child_age < settle_seconds:
                return False
    return True


def _entry_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for child in path.rglob("*"):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                except FileNotFoundError:
                    continue
            return total
    except FileNotFoundError:
        return 0
    return 0


def describe_downloads(config: MonitorConfig) -> List[DownloadEntry]:
    entries: List[DownloadEntry] = []
    source = config.source_dir
    if not source.exists():
        return entries
    for entry in source.iterdir():
        if entry.name.startswith("."):
            continue
        if not entry.exists():
            continue
        try:
            stat = entry.stat()
        except FileNotFoundError:
            continue
        ready = _entry_is_ready(entry, config.settle_seconds)
        entries.append(
            DownloadEntry(
                name=entry.name,
                path=entry,
                type="directory" if entry.is_dir() else "file",
                size=_entry_size(entry),
                modified_ts=stat.st_mtime,
                ready=ready,
            )
        )
    entries.sort(key=lambda item: item.modified_ts, reverse=True)
    return entries
