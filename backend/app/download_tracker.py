import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    get_app_config,
    get_sabnzbd_connection,
    list_active_download_jobs,
    mark_download_job_failed,
    update_download_job_status,
)
from .settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackerConfig:
    poll_interval: float = 10.0
    history_limit: int = 50
    debug_logging: bool = False


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
            "Started download tracker: poll=%ss history=%s debug=%s",
            self.config.poll_interval,
            self.config.history_limit,
            self.config.debug_logging,
        )

    def update_config(self, config: TrackerConfig) -> None:
        self.config = config
        logger.info(
            "Updated download tracker: poll=%ss history=%s debug=%s",
            self.config.poll_interval,
            self.config.history_limit,
            self.config.debug_logging,
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
            app_config = get_app_config(session)

        queue_items = await fetch_queue(connection)
        history_items = await fetch_history(connection, limit=max(self.config.history_limit, len(queue_items) + 20))
        queue_map = {item.nzo_id: item for item in queue_items if item.nzo_id}
        history_map = {item.nzo_id: item for item in history_items if item.nzo_id}
        if self.config.debug_logging:
            self._log_debug_snapshot(queue_items, history_items)

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
                    if self.config.debug_logging:
                        self._log_job_update(
                            job_id=job.id,
                            nzo_id=nzo_id,
                            source="queue",
                            status=status,
                            sab_status=queue_item.status,
                            progress=queue_item.percentage,
                            time_remaining=queue_item.timeleft,
                            message=message,
                            content=queue_item.filename,
                        )
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
                    if self.config.debug_logging:
                        self._log_job_update(
                            job_id=job.id,
                            nzo_id=nzo_id,
                            source="history",
                            status=status,
                            sab_status=history_item.status,
                            progress=100.0 if status == "completed" else job.progress,
                            time_remaining=None,
                            message=message,
                            content=history_item.name,
                        )
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
                elif self.config.debug_logging:
                    logger.debug(
                        "Download tracker could not locate SAB entry for job_id=%s nzo_id=%s title=%s status=%s",
                        job.id,
                        nzo_id,
                        job.title,
                        job.status,
                    )
            self._auto_fail_jobs(session, app_config)

    @staticmethod
    def _map_queue_status(status: Optional[str]) -> str:
        if not status:
            return "queued"
        value = status.lower()
        if "down" in value:
            return "downloading"
        if "post" in value or "check" in value or "extract" in value:
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

    def _auto_fail_jobs(self, session: Session, app_config) -> None:
        if not getattr(app_config, "auto_fail_enabled", False):
            return
        threshold_minutes = app_config.auto_fail_minutes or get_settings().auto_fail_minutes
        if threshold_minutes <= 0:
            return
        threshold = timedelta(minutes=threshold_minutes)
        now = datetime.utcnow()
        watched_statuses = {"pending", "queued", "downloading", "processing", "paused"}
        for job in list_active_download_jobs(session):
            if job.status not in watched_statuses:
                continue
            reference = job.last_seen or job.updated_at or job.created_at
            if not reference:
                continue
            if now - reference < threshold:
                continue
            mark_download_job_failed(
                session,
                job,
                message=f"Auto-failed after {threshold_minutes:g}m without SABnzbd progress.",
            )

    def _log_debug_snapshot(self, queue_items, history_items) -> None:
        if queue_items:
            for item in queue_items:
                logger.debug(
                    "SAB queue item: nzo_id=%s status=%s perc=%s timeleft=%s filename=%s",
                    item.nzo_id,
                    item.status,
                    item.percentage,
                    item.timeleft,
                    item.filename,
                )
        else:
            logger.debug("SAB queue empty.")
        if history_items:
            for item in history_items:
                logger.debug(
                    "SAB history item: nzo_id=%s status=%s completed=%s name=%s fail=%s",
                    item.nzo_id,
                    item.status,
                    item.completed,
                    item.name,
                    item.fail_message,
                )
        else:
            logger.debug("SAB history snapshot empty.")

    @staticmethod
    def _log_job_update(
        *,
        job_id: Optional[int],
        nzo_id: Optional[str],
        source: str,
        status: Optional[str],
        sab_status: Optional[str],
        progress: Optional[float],
        time_remaining: Optional[str],
        message: Optional[str],
        content: Optional[str],
    ) -> None:
        logger.debug(
            "Tracker update (%s): job_id=%s nzo_id=%s status=%s sab_status=%s progress=%s time_remaining=%s message=%s content=%s",
            source,
            job_id,
            nzo_id,
            status,
            sab_status,
            progress,
            time_remaining,
            message,
            content,
        )
