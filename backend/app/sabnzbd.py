from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx


class SabnzbdNotConfigured(RuntimeError):
    """Raised when SABnzbd settings are missing."""


class SabnzbdError(RuntimeError):
    """Raised when SABnzbd returns an error response."""


@dataclass
class SabnzbdConnection:
    base_url: str
    api_key: str
    category: Optional[str] = None
    priority: Optional[int] = None
    timeout: int = 10


@dataclass
class SabnzbdQueueResult:
    nzo_ids: List[str]
    response: Dict[str, Any]

    @property
    def message(self) -> str:
        return "Request queued in SABnzbd"


@dataclass
class SabnzbdTestResult:
    success: bool
    message: str
    response: Dict[str, Any]


@dataclass
class SabnzbdQueueItem:
    nzo_id: Optional[str]
    filename: Optional[str]
    status: Optional[str]
    percentage: Optional[float]
    timeleft: Optional[str]


@dataclass
class SabnzbdHistoryItem:
    nzo_id: Optional[str]
    name: Optional[str]
    status: Optional[str]
    completed: Optional[datetime]
    fail_message: Optional[str]


def is_configured(connection: Optional[SabnzbdConnection]) -> bool:
    return bool(connection and connection.base_url and connection.api_key)


def _build_api_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


async def enqueue_url(
    nzb_url: str,
    title: Optional[str] = None,
    connection: Optional[SabnzbdConnection] = None,
) -> SabnzbdQueueResult:
    if not is_configured(connection):
        raise SabnzbdNotConfigured("SABnzbd connection is not configured.")

    assert connection is not None
    conn = connection
    api_url = _build_api_url(conn.base_url)
    params: Dict[str, Any] = {
        "mode": "addurl",
        "name": nzb_url,
        "apikey": conn.api_key,
        "output": "json",
    }
    if title:
        params["nzbname"] = title
    if conn.category:
        params["cat"] = conn.category
    if conn.priority is not None:
        params["priority"] = str(conn.priority)

    async with httpx.AsyncClient(timeout=conn.timeout) as client:
        response = await client.post(api_url, params=params)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SabnzbdError("Invalid JSON response from SABnzbd.") from exc

    status_value = payload.get("status")
    if isinstance(status_value, str):
        status_ok = status_value.lower() in {"true", "1", "ok", "success"}
    else:
        status_ok = bool(status_value)

    if not status_ok:
        message = payload.get("error") or payload.get("error_message") or "SABnzbd rejected the NZB."
        raise SabnzbdError(str(message))

    nzo_ids_raw = payload.get("nzo_ids") or []
    if isinstance(nzo_ids_raw, list):
        nzo_ids = [str(item) for item in nzo_ids_raw]
    else:
        nzo_ids = [str(nzo_ids_raw)]

    return SabnzbdQueueResult(nzo_ids=nzo_ids, response=payload)


async def test_connection(connection: Optional[SabnzbdConnection] = None) -> SabnzbdTestResult:
    if not is_configured(connection):
        raise SabnzbdNotConfigured("SABnzbd connection is not configured.")

    assert connection is not None
    conn = connection
    api_url = _build_api_url(conn.base_url)
    params: Dict[str, Any] = {
        "mode": "auth",
        "apikey": conn.api_key,
        "output": "json",
    }

    async with httpx.AsyncClient(timeout=conn.timeout) as client:
        response = await client.get(api_url, params=params)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SabnzbdError("Invalid JSON response from SABnzbd.") from exc

    status_value = payload.get("status")
    if status_value is None and payload.get("auth") is not None:
        status_ok = True
    elif isinstance(status_value, str):
        status_ok = status_value.lower() in {"true", "1", "ok", "success"}
    else:
        status_ok = bool(status_value)

    if not status_ok:
        message = payload.get("error") or payload.get("error_message") or "SABnzbd authentication failed."
        raise SabnzbdError(str(message))

    message = payload.get("msg") or payload.get("auth") or "Connection successful."
    return SabnzbdTestResult(success=True, message=str(message), response=payload)


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def fetch_queue(connection: Optional[SabnzbdConnection] = None) -> List[SabnzbdQueueItem]:
    if not is_configured(connection):
        raise SabnzbdNotConfigured("SABnzbd connection is not configured.")

    assert connection is not None
    conn = connection
    api_url = _build_api_url(conn.base_url)
    params: Dict[str, Any] = {
        "mode": "queue",
        "apikey": conn.api_key,
        "output": "json",
    }

    async with httpx.AsyncClient(timeout=conn.timeout) as client:
        response = await client.get(api_url, params=params)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SabnzbdError("Invalid JSON response from SABnzbd.") from exc

    queue_data = payload.get("queue") or {}
    slots = queue_data.get("slots") or []
    items: List[SabnzbdQueueItem] = []
    for slot in slots:
        items.append(
            SabnzbdQueueItem(
                nzo_id=slot.get("nzo_id"),
                filename=slot.get("filename") or slot.get("title"),
                status=slot.get("status"),
                percentage=_safe_float(slot.get("percentage")),
                timeleft=slot.get("timeleft"),
            )
        )
    return items


async def fetch_history(
    connection: Optional[SabnzbdConnection] = None,
    limit: int = 50,
) -> List[SabnzbdHistoryItem]:
    if not is_configured(connection):
        raise SabnzbdNotConfigured("SABnzbd connection is not configured.")

    assert connection is not None
    conn = connection
    api_url = _build_api_url(conn.base_url)
    params: Dict[str, Any] = {
        "mode": "history",
        "apikey": conn.api_key,
        "output": "json",
        "limit": str(limit),
    }

    async with httpx.AsyncClient(timeout=conn.timeout) as client:
        response = await client.get(api_url, params=params)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SabnzbdError("Invalid JSON response from SABnzbd.") from exc

    history_data = payload.get("history") or {}
    slots = history_data.get("slots") or []
    items: List[SabnzbdHistoryItem] = []
    for slot in slots:
        completed_ts = slot.get("completed")
        completed_dt: Optional[datetime] = None
        if isinstance(completed_ts, (int, float)):
            completed_dt = datetime.fromtimestamp(completed_ts, tz=timezone.utc)
        elif isinstance(completed_ts, str):
            try:
                completed_dt = datetime.fromtimestamp(int(completed_ts), tz=timezone.utc)
            except (TypeError, ValueError):
                completed_dt = None
        items.append(
            SabnzbdHistoryItem(
                nzo_id=slot.get("nzo_id"),
                name=slot.get("name") or slot.get("title"),
                status=slot.get("status"),
                completed=completed_dt,
                fail_message=slot.get("fail_message"),
            )
        )
    return items
