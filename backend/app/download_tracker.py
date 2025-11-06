import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session

from .database import engine
from .sabnzbd import (
    SabnzbdConnection,
    SabnzbdError,
    SabnzbdNotConfigured,
    fetch_history,
    fetch_queue,
)
from .services import (
    get_sabnzbd_connection,
    list_active_download_jobs,
    update_download_job_status,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackerConfig:
    poll_interval: float = 10.0
    history_limit: int = 50


class DownloadTracker:
    """Polls SABnzbd for job updates and syncs them to the database."""

    def __init__(self, config: TrackerConfig) -> None:
        self.config = config
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="gazarr-download-tracker")
        logger.info(
            "Started download tracker: poll=%ss history=%s",
            self.config.poll_interval,
            self.config.history_limit,
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
            logger.info("Stopped download tracker.")

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._sync_once()
                except SabnzbdNotConfigured:
                    # No configuration yet – wait for next cycle.
                    await asyncio.sleep(self.config.poll_interval)
                except SabnzbdError:
                    logger.exception("Download tracker failed to query SABnzbd.")
                except Exception:  # pragma: no cover - defensive
                    logger.exception("Download tracker encountered an unexpected error.")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.poll_interval)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise

    async def _sync_once(self) -> None:
        with Session(engine) as session:
            jobs = list_active_download_jobs(session)
            if not jobs:
                return
            connection: Optional[SabnzbdConnection] = get_sabnzbd_connection(session)
            if not connection:
                raise SabnzbdNotConfigured("SABnzbd connection missing.")

        queue_items = await fetch_queue(connection)
        history_items = await fetch_history(connection, limit=max(self.config.history_limit, len(queue_items) + 20))
        queue_map = {item.nzo_id: item for item in queue_items if item.nzo_id}
        history_map = {item.nzo_id: item for item in history_items if item.nzo_id}

        with Session(engine) as session:
            for job in list_active_download_jobs(session):
                nzo_id = job.sabnzbd_id
                if not nzo_id:
                    continue
                queue_item = queue_map.get(nzo_id)
                if queue_item:
                    status = self._map_queue_status(queue_item.status)
                    message = queue_item.timeleft or job.message
                    if status == "queued" and queue_item.timeleft:
                        message = f"{queue_item.timeleft} remaining"
                    elif status == "downloading" and queue_item.timeleft:
                        message = f"{queue_item.timeleft} remaining"
                    update_download_job_status(
                        session,
                        job,
                        status=status,
                        sab_status=queue_item.status,
                        progress=queue_item.percentage,
                        time_remaining=queue_item.timeleft,
                        message=message,
                        content_name=queue_item.filename,
                    )
                    continue

                history_item = history_map.get(nzo_id)
                if history_item:
                    status = self._map_history_status(history_item.status)
                    message = history_item.fail_message
                    if status == "completed":
                        message = "Awaiting import into library"
                    update_download_job_status(
                        session,
                        job,
                        status=status,
                        sab_status=history_item.status,
                        progress=100.0 if status == "completed" else job.progress,
                        time_remaining=None,
                        message=message,
                        content_name=history_item.name,
                        completed_at=history_item.completed,
                    )

    @staticmethod
    def _map_queue_status(status: Optional[str]) -> str:
        if not status:
            return "queued"
        value = status.lower()
        if "down" in value:
            return "downloading"
        if "post" in value or "check" in value:
            return "processing"
        if "pause" in value:
            return "paused"
        if "queued" in value or "waiting" in value:
            return "queued"
        return value.replace(" ", "_")

    @staticmethod
    def _map_history_status(status: Optional[str]) -> str:
        if not status:
            return "completed"
        value = status.lower()
        if value == "completed":
            return "completed"
        if value in {"failed", "failure"}:
            return "failed"
        return value.replace(" ", "_")
