import binascii
import logging
import secrets
from base64 import b64decode
from datetime import datetime, timezone
from typing import List, Optional
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from sqlmodel import Session

from .database import get_session, init_db
from .models import Magazine, Provider, SabnzbdConfig
from .schemas import (
    HealthResponse,
    MagazineCreate,
    MagazineRead,
    MagazineUpdate,
    ProviderCreate,
    ProviderRead,
    ProviderUpdate,
    SearchRequest,
    SearchResult,
    SabnzbdConfigRead,
    SabnzbdConfigUpdate,
    DownloadQueueEntry,
    DownloadJobRead,
    DownloadQueueResponse,
    SabnzbdEnqueueRequest,
    SabnzbdEnqueueResponse,
    SabnzbdStatus,
    SabnzbdTestResponse,
)
from .settings import get_settings
from .sabnzbd import (
    SabnzbdError,
    SabnzbdNotConfigured,
    enqueue_url,
    test_connection,
    is_configured as sabnzbd_configured,
)
from .download_monitor import DownloadMonitor, MonitorConfig, describe_downloads
from .download_tracker import DownloadTracker, TrackerConfig
from .services import (
    create_magazine,
    create_provider,
    delete_magazine,
    delete_provider,
    get_magazine,
    get_provider,
    get_sabnzbd_config,
    get_sabnzbd_connection,
    list_magazines,
    list_providers,
    list_recent_download_jobs,
    upsert_download_job,
    list_recent_download_jobs,
    search_magazines,
    update_sabnzbd_config,
    update_magazine,
    update_provider,
)

app = FastAPI(title="Gazarr API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Global HTTP Basic authentication enforced for every request, including static files."""

    def __init__(self, app, *, username: str, password: str, realm: str = "Gazarr") -> None:
        super().__init__(app)
        self._username = username
        self._password = password
        self._realm = realm

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        header = request.headers.get("Authorization")
        if not header or not header.startswith("Basic "):
            return self._challenge()
        token = header.split(" ", 1)[1]
        try:
            decoded = b64decode(token, validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            return self._challenge()
        username, sep, password = decoded.partition(":")
        if not sep:
            return self._challenge()
        if not (secrets.compare_digest(username, self._username) and secrets.compare_digest(password, self._password)):
            return self._challenge()
        return await call_next(request)

    def _challenge(self) -> Response:
        return Response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": f'Basic realm="{self._realm}"'},
        )


def _configure_basic_auth(app: FastAPI) -> None:
    settings = get_settings()
    username = settings.auth_username
    password = settings.auth_password
    if not username or not password:
        raise RuntimeError(
            "Basic authentication requires both GAZARR_AUTH_USERNAME and GAZARR_AUTH_PASSWORD environment variables."
        )
    realm = settings.app_name or "Gazarr"
    app.add_middleware(BasicAuthMiddleware, username=username, password=password, realm=realm)


_configure_basic_auth(app)


def _setup_download_monitor() -> Optional[DownloadMonitor]:
    settings = get_settings()
    downloads_dir = settings.downloads_dir
    library_dir = settings.library_dir
    if not downloads_dir or not library_dir:
        return None

    config = MonitorConfig(
        source_dir=downloads_dir.expanduser(),
        target_dir=library_dir.expanduser(),
        staging_dir=settings.staging_dir.expanduser() if settings.staging_dir else None,
        poll_interval=settings.downloads_poll_interval,
        settle_seconds=settings.downloads_settle_seconds,
        cover_dir=settings.covers_dir.expanduser() if settings.covers_dir else None,
    )
    monitor = DownloadMonitor(config)
    monitor.start()
    return monitor


def _setup_download_tracker() -> DownloadTracker:
    settings = get_settings()
    config = TrackerConfig(
        poll_interval=settings.download_tracker_poll_interval,
        history_limit=settings.download_tracker_history_limit,
    )
    tracker = DownloadTracker(config)
    tracker.start()
    return tracker


@app.on_event("startup")
async def startup_event() -> None:
    init_db()
    monitor = _setup_download_monitor()
    if monitor:
        app.state.download_monitor = monitor
        logger.info("Download monitor enabled.")
    else:
        logger.info("Download monitor disabled (missing downloads or library directory).")
    tracker = _setup_download_tracker()
    app.state.download_tracker = tracker


@app.on_event("shutdown")
async def shutdown_event() -> None:
    monitor: Optional[DownloadMonitor] = getattr(app.state, "download_monitor", None)
    if monitor:
        await monitor.stop()
    tracker: Optional[DownloadTracker] = getattr(app.state, "download_tracker", None)
    if tracker:
        await tracker.stop()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


# Providers --------------------------------------------------------------------


@app.get("/providers", response_model=List[ProviderRead])
def read_providers(session: Session = Depends(get_session)) -> List[Provider]:
    return list_providers(session)


@app.post("/providers", response_model=ProviderRead, status_code=status.HTTP_201_CREATED)
def create_provider_endpoint(payload: ProviderCreate, session: Session = Depends(get_session)) -> Provider:
    return create_provider(session, payload)


@app.patch("/providers/{provider_id}", response_model=ProviderRead)
def update_provider_endpoint(
    provider_id: int, payload: ProviderUpdate, session: Session = Depends(get_session)
) -> Provider:
    provider = get_provider(session, provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return update_provider(session, provider, payload)


@app.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider_endpoint(provider_id: int, session: Session = Depends(get_session)) -> None:
    provider = get_provider(session, provider_id)
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    delete_provider(session, provider)


# Magazines --------------------------------------------------------------------


@app.get("/magazines", response_model=List[MagazineRead])
def read_magazines(status_filter: Optional[str] = None, session: Session = Depends(get_session)) -> List[Magazine]:
    return list_magazines(session, status=status_filter)


@app.post("/magazines", response_model=MagazineRead, status_code=status.HTTP_201_CREATED)
def create_magazine_endpoint(payload: MagazineCreate, session: Session = Depends(get_session)) -> Magazine:
    return create_magazine(session, payload)


@app.patch("/magazines/{magazine_id}", response_model=MagazineRead)
def update_magazine_endpoint(
    magazine_id: int, payload: MagazineUpdate, session: Session = Depends(get_session)
) -> Magazine:
    magazine = get_magazine(session, magazine_id)
    if not magazine:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")
    return update_magazine(session, magazine, payload)


@app.delete("/magazines/{magazine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_magazine_endpoint(magazine_id: int, session: Session = Depends(get_session)) -> None:
    magazine = get_magazine(session, magazine_id)
    if not magazine:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magazine not found")
    delete_magazine(session, magazine)


# Search -----------------------------------------------------------------------


@app.post("/magazines/search", response_model=List[SearchResult])
async def search_magazines_endpoint(payload: SearchRequest, session: Session = Depends(get_session)) -> List[SearchResult]:
    return await search_magazines(session, titles=payload.titles)


@app.get("/sabnzbd/config", response_model=SabnzbdConfigRead)
def read_sabnzbd_config_endpoint(session: Session = Depends(get_session)) -> SabnzbdConfig:
    return get_sabnzbd_config(session)


@app.patch("/sabnzbd/config", response_model=SabnzbdConfigRead)
def update_sabnzbd_config_endpoint(
    payload: SabnzbdConfigUpdate, session: Session = Depends(get_session)
) -> SabnzbdConfig:
    return update_sabnzbd_config(session, payload)


@app.get("/sabnzbd/status", response_model=SabnzbdStatus)
def sabnzbd_status_endpoint(session: Session = Depends(get_session)) -> SabnzbdStatus:
    config = get_sabnzbd_config(session)
    connection = get_sabnzbd_connection(session, config=config)
    base_url = connection.base_url if connection else config.base_url
    return SabnzbdStatus(
        enabled=sabnzbd_configured(connection),
        base_url=base_url,
        category=config.category,
    )


@app.get("/downloads", response_model=DownloadQueueResponse)
def list_downloads_endpoint(session: Session = Depends(get_session)) -> DownloadQueueResponse:
    settings = get_settings()
    downloads_dir = settings.downloads_dir
    library_dir = settings.library_dir
    jobs = list_recent_download_jobs(session)
    job_payload = [DownloadJobRead.model_validate(job) for job in jobs]
    if not downloads_dir or not library_dir:
        return DownloadQueueResponse(enabled=False, entries=[], jobs=job_payload)

    config = MonitorConfig(
        source_dir=downloads_dir.expanduser(),
        target_dir=library_dir.expanduser(),
        staging_dir=settings.staging_dir.expanduser() if settings.staging_dir else None,
        poll_interval=settings.downloads_poll_interval,
        settle_seconds=settings.downloads_settle_seconds,
    )
    entries = []
    for item in describe_downloads(config):
        entries.append(
            DownloadQueueEntry(
                name=item.name,
                type=item.type,
                size=item.size,
                modified=datetime.fromtimestamp(item.modified_ts, tz=timezone.utc),
                ready=item.ready,
            )
        )
    return DownloadQueueResponse(enabled=True, entries=entries, jobs=job_payload)


@app.post("/sabnzbd/download", response_model=SabnzbdEnqueueResponse)
async def sabnzbd_download_endpoint(
    payload: SabnzbdEnqueueRequest,
    session: Session = Depends(get_session),
) -> SabnzbdEnqueueResponse:
    connection = get_sabnzbd_connection(session)
    if not sabnzbd_configured(connection):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="SABnzbd connection is not configured.",
        )
    try:
        result = await enqueue_url(str(payload.link), title=payload.title, connection=connection)
    except SabnzbdNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc
    except SabnzbdError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SABnzbd request failed: {exc!s}",
        ) from exc
    str_link = str(payload.link)
    meta = payload.metadata.model_dump(exclude_none=True) if payload.metadata else {}
    for nzo_id in result.nzo_ids:
        upsert_download_job(
            session,
            nzo_id=nzo_id,
            title=payload.title or None,
            magazine_title=meta.get("magazine_title"),
            link=str_link,
            status="queued",
            issue_code=meta.get("issue_code"),
            issue_label=meta.get("issue_label"),
            issue_year=meta.get("issue_year"),
            issue_month=meta.get("issue_month"),
            issue_number=meta.get("issue_number"),
        )
    return SabnzbdEnqueueResponse(nzo_ids=result.nzo_ids, message=result.message)


@app.post("/sabnzbd/test", response_model=SabnzbdTestResponse)
async def sabnzbd_test_endpoint(session: Session = Depends(get_session)) -> SabnzbdTestResponse:
    connection = get_sabnzbd_connection(session)
    if not sabnzbd_configured(connection):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="SABnzbd connection is not configured.",
        )
    try:
        result = await test_connection(connection)
    except SabnzbdNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc
    except SabnzbdError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"SABnzbd request failed: {exc!s}",
        ) from exc
    return SabnzbdTestResponse(ok=result.success, message=result.message)


# Static frontend --------------------------------------------------------------

# Directory populated by Docker build: copied to /app/static in the image
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

if _STATIC_DIR.exists():
    # Mount SPA at root; API routes defined above still take precedence.
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="frontend")
