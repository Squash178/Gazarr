import asyncio
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .models import DownloadJob, Magazine, Provider, SabnzbdConfig
from .schemas import (
    MagazineCreate,
    MagazineUpdate,
    ProviderCreate,
    ProviderUpdate,
    SabnzbdConfigUpdate,
    SearchResult,
)
from .settings import get_settings
from .sabnzbd import SabnzbdConnection
from .issue_parser import parse_issue

NEWZNAB_NS = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}


# Provider CRUD ----------------------------------------------------------------

def list_providers(session: Session) -> List[Provider]:
    return list(session.exec(select(Provider)))


def get_provider(session: Session, provider_id: int) -> Optional[Provider]:
    return session.get(Provider, provider_id)


def create_provider(session: Session, payload: ProviderCreate) -> Provider:
    data = payload.model_dump()
    data["base_url"] = str(payload.base_url)
    provider = Provider(**data)
    now = datetime.utcnow()
    provider.created_at = now
    provider.updated_at = now
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return provider


def update_provider(session: Session, provider: Provider, payload: ProviderUpdate) -> Provider:
    data = payload.model_dump(exclude_unset=True)
    if "base_url" in data and data["base_url"] is not None:
        data["base_url"] = str(data["base_url"])
    for key, value in data.items():
        setattr(provider, key, value)
    provider.updated_at = datetime.utcnow()
    session.add(provider)
    session.commit()
    session.refresh(provider)
    return provider


def delete_provider(session: Session, provider: Provider) -> None:
    session.delete(provider)
    session.commit()


# Magazine CRUD -----------------------------------------------------------------

def list_magazines(session: Session, status: Optional[str] = None) -> List[Magazine]:
    statement = select(Magazine)
    if status:
        statement = statement.where(Magazine.status == status)
    magazines = list(session.exec(statement))
    for magazine in magazines:
        if not getattr(magazine, "language", None):
            magazine.language = "en"
    return magazines


def get_magazine(session: Session, magazine_id: int) -> Optional[Magazine]:
    return session.get(Magazine, magazine_id)


def create_magazine(session: Session, payload: MagazineCreate) -> Magazine:
    data = payload.model_dump()
    if not data.get("language"):
        data["language"] = "en"
    magazine = Magazine(**data)
    now = datetime.utcnow()
    magazine.created_at = now
    magazine.updated_at = now
    session.add(magazine)
    session.commit()
    session.refresh(magazine)
    return magazine


def update_magazine(session: Session, magazine: Magazine, payload: MagazineUpdate) -> Magazine:
    data = payload.model_dump(exclude_unset=True)
    if data.get("language") is None:
        data["language"] = magazine.language or "en"
    for key, value in data.items():
        setattr(magazine, key, value)
    magazine.updated_at = datetime.utcnow()
    session.add(magazine)
    session.commit()
    session.refresh(magazine)
    return magazine


def delete_magazine(session: Session, magazine: Magazine) -> None:
    session.delete(magazine)
    session.commit()


# Search ------------------------------------------------------------------------

def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _result_sort_key(result: SearchResult) -> tuple:
    if result.issue_year:
        year = _safe_int(result.issue_year)
        month = _safe_int(result.issue_month)
        day = _safe_int(result.issue_day)
        issue_num = _safe_int(result.issue_number)
        published_ts = int(result.published.timestamp()) if result.published else 0
        return (year, month, day, issue_num, published_ts)
    if result.published:
        published = result.published
        published_ts = int(published.timestamp())
        return (published.year, published.month, published.day, 0, published_ts)
    issue_num = _safe_int(result.issue_number)
    return (0, 0, 0, issue_num, 0)


def _build_search_term(magazine: Magazine) -> str:
    if magazine.regex:
        return magazine.regex
    return magazine.title


def _iter_search_terms(magazines: Iterable[Magazine]) -> Iterable[Tuple[Magazine, str]]:
    for magazine in magazines:
        term = _build_search_term(magazine)
        if term:
            yield magazine, term


async def search_magazines(
    session: Session,
    titles: Optional[List[str]] = None,
) -> List[SearchResult]:
    """Fetch NZB results for magazines using enabled Torznab/Newznab providers."""

    settings = get_settings()
    providers = session.exec(
        select(Provider).where(Provider.enabled == True)  # noqa: E712
    ).all()

    if titles:
        magazines = session.exec(
            select(Magazine).where(Magazine.title.in_(titles), Magazine.status == "active")
        ).all()
    else:
        magazines = session.exec(select(Magazine).where(Magazine.status == "active")).all()

    if not magazines or not providers:
        return []

    async with httpx.AsyncClient(timeout=settings.torznab_timeout) as client:
        tasks = []
        for provider in providers:
            if "M" not in provider.download_types.upper():
                continue
            for magazine, term in _iter_search_terms(magazines):
                tasks.append(_query_provider(client, provider, magazine, term))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: List[SearchResult] = []
    max_age_days = settings.torznab_max_age_days
    max_age = timedelta(days=max_age_days) if max_age_days else None
    for item in raw_results:
        if isinstance(item, Exception):
            continue
        for result in item:
            if max_age and result.published:
                if datetime.utcnow() - result.published > max_age:
                    continue
            results.append(result)
    if results:
        results.sort(key=_result_sort_key, reverse=True)
    return results


async def _query_provider(
    client: httpx.AsyncClient,
    provider: Provider,
    magazine: Magazine,
    term: str,
) -> List[SearchResult]:
    params = {"apikey": provider.api_key, "t": "search", "q": term}
    url = _normalise_provider_url(provider.base_url)
    response = await client.get(url, params=params)
    response.raise_for_status()
    return _parse_torznab_response(
        provider.name,
        response.text,
        magazine_title=magazine.title,
        magazine_language=magazine.language or "en",
    )


def _normalise_provider_url(base_url: str) -> str:
    if base_url.endswith("/api"):
        return base_url
    if base_url.endswith("/"):
        return f"{base_url.rstrip('/')}/api"
    return f"{base_url}/api"


def _parse_torznab_response(
    provider_name: str,
    xml_payload: str,
    magazine_title: Optional[str] = None,
    magazine_language: str = "en",
) -> List[SearchResult]:
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return []

    results: List[SearchResult] = []
    for item in root.findall("./channel/item"):
        title = item.findtext("title")
        link = item.findtext("link")
        if not title or not link:
            continue

        published = None
        pub_text = item.findtext("pubDate")
        if pub_text:
            try:
                published = parsedate_to_datetime(pub_text)
            except (TypeError, ValueError):
                published = None

        size = None
        categories: List[str] = []
        for attr in item.findall("newznab:attr", NEWZNAB_NS):
            name = attr.attrib.get("name")
            value = attr.attrib.get("value")
            if not name or value is None:
                continue
            if name == "size":
                try:
                    size = int(value)
                except ValueError:
                    size = None
            elif name == "category":
                categories.append(value)

        metadata = parse_issue(title, magazine_title, language=magazine_language)
        result = SearchResult(
            provider=provider_name,
            title=title,
            link=link,
            published=published,
            size=size,
            categories=categories,
            magazine_title=magazine_title,
            issue_code=metadata.issue_code if metadata else None,
            issue_label=metadata.label if metadata else None,
            issue_year=metadata.year if metadata else None,
            issue_month=metadata.month if metadata else None,
            issue_day=metadata.day if metadata else None,
            issue_number=metadata.issue_number if metadata else None,
            issue_volume=metadata.volume if metadata else None,
        )
        results.append(result)
    return results


# SABnzbd configuration --------------------------------------------------------

def _clean_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_sabnzbd_config(session: Session) -> SabnzbdConfig:
    config = session.exec(select(SabnzbdConfig).limit(1)).first()
    if config:
        return config
    settings = get_settings()
    now = datetime.utcnow()
    config = SabnzbdConfig(
        base_url=_clean_optional_str(settings.sabnzbd_url),
        api_key=_clean_optional_str(settings.sabnzbd_api_key),
        category=_clean_optional_str(settings.sabnzbd_category),
        priority=settings.sabnzbd_priority,
        timeout=settings.sabnzbd_timeout,
        created_at=now,
        updated_at=now,
    )
    session.add(config)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        config = session.exec(select(SabnzbdConfig).limit(1)).first()
        if config:
            return config
        raise
    session.refresh(config)
    return config


def update_sabnzbd_config(session: Session, payload: SabnzbdConfigUpdate) -> SabnzbdConfig:
    config = get_sabnzbd_config(session)
    data = payload.model_dump(exclude_unset=True)

    if "base_url" in data:
        data["base_url"] = _clean_optional_str(data["base_url"])
    if "api_key" in data:
        data["api_key"] = _clean_optional_str(data["api_key"])
    if "category" in data:
        data["category"] = _clean_optional_str(data["category"])

    for key, value in data.items():
        setattr(config, key, value)

    config.updated_at = datetime.utcnow()
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def get_sabnzbd_connection(session: Session, config: Optional[SabnzbdConfig] = None) -> Optional[SabnzbdConnection]:
    config = config or get_sabnzbd_config(session)
    if not config.base_url or not config.api_key:
        return None
    timeout = config.timeout or get_settings().sabnzbd_timeout
    return SabnzbdConnection(
        base_url=config.base_url,
        api_key=config.api_key,
        category=config.category,
        priority=config.priority,
        timeout=timeout,
    )


# Download jobs ----------------------------------------------------------------

def get_download_job_by_nzo(session: Session, nzo_id: str) -> Optional[DownloadJob]:
    return session.exec(select(DownloadJob).where(DownloadJob.sabnzbd_id == nzo_id)).first()


def upsert_download_job(
    session: Session,
    *,
    nzo_id: Optional[str],
    title: Optional[str],
    magazine_title: Optional[str],
    link: Optional[str],
    status: str = "pending",
    issue_code: Optional[str] = None,
    issue_label: Optional[str] = None,
    issue_year: Optional[int] = None,
    issue_month: Optional[int] = None,
    issue_number: Optional[int] = None,
) -> DownloadJob:
    now = datetime.utcnow()
    job: Optional[DownloadJob] = None
    if nzo_id:
        job = get_download_job_by_nzo(session, nzo_id)
    if not job:
        job = DownloadJob(
            sabnzbd_id=nzo_id,
            title=title,
            magazine_title=magazine_title,
            link=link,
            status=status,
            issue_code=issue_code,
            issue_label=issue_label,
            issue_year=issue_year,
            issue_month=issue_month,
            issue_number=issue_number,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    changed = False
    if title and job.title != title:
        job.title = title
        changed = True
    if magazine_title is not None and job.magazine_title != magazine_title:
        job.magazine_title = magazine_title
        changed = True
    if link and job.link != link:
        job.link = link
        changed = True
    if status and job.status != status:
        job.status = status
        changed = True
    if issue_code is not None and job.issue_code != issue_code:
        job.issue_code = issue_code
        changed = True
    if issue_label is not None and job.issue_label != issue_label:
        job.issue_label = issue_label
        changed = True
    if issue_year is not None and job.issue_year != issue_year:
        job.issue_year = issue_year
        changed = True
    if issue_month is not None and job.issue_month != issue_month:
        job.issue_month = issue_month
        changed = True
    if issue_number is not None and job.issue_number != issue_number:
        job.issue_number = issue_number
        changed = True

    if changed:
        job.updated_at = now

    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_download_job_status(
    session: Session,
    job: DownloadJob,
    *,
    status: Optional[str] = None,
    sab_status: Optional[str] = None,
    progress: Optional[float] = None,
    time_remaining: Optional[str] = None,
    message: Optional[str] = None,
    content_name: Optional[str] = None,
    completed_at: Optional[datetime] = None,
    clean_name: Optional[str] = None,
    thumbnail_path: Optional[str] = None,
    staging_path: Optional[str] = None,
    magazine_title: Optional[str] = None,
    issue_code: Optional[str] = None,
    issue_label: Optional[str] = None,
    issue_year: Optional[int] = None,
    issue_month: Optional[int] = None,
    issue_number: Optional[int] = None,
) -> DownloadJob:
    now = datetime.utcnow()
    updated = False

    if status and job.status != status:
        job.status = status
        updated = True
    if sab_status is not None and job.sab_status != sab_status:
        job.sab_status = sab_status
        updated = True
    if progress is not None and job.progress != progress:
        job.progress = progress
        updated = True
    if time_remaining is not None and job.time_remaining != time_remaining:
        job.time_remaining = time_remaining
        updated = True
    if message is not None and job.message != message:
        job.message = message
        updated = True
    if content_name and job.content_name != content_name:
        job.content_name = content_name
        updated = True
    if completed_at and job.completed_at != completed_at:
        job.completed_at = completed_at
        updated = True
    if clean_name and job.clean_name != clean_name:
        job.clean_name = clean_name
        updated = True
    if thumbnail_path and job.thumbnail_path != thumbnail_path:
        job.thumbnail_path = thumbnail_path
        updated = True
    if staging_path and job.staging_path != staging_path:
        job.staging_path = staging_path
        updated = True
    if magazine_title is not None and job.magazine_title != magazine_title:
        job.magazine_title = magazine_title
        updated = True
    if issue_code is not None and job.issue_code != issue_code:
        job.issue_code = issue_code
        updated = True
    if issue_label is not None and job.issue_label != issue_label:
        job.issue_label = issue_label
        updated = True
    if issue_year is not None and job.issue_year != issue_year:
        job.issue_year = issue_year
        updated = True
    if issue_month is not None and job.issue_month != issue_month:
        job.issue_month = issue_month
        updated = True
    if issue_number is not None and job.issue_number != issue_number:
        job.issue_number = issue_number
        updated = True

    job.last_seen = now
    if updated:
        job.updated_at = now

    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def mark_download_job_failed(session: Session, job: DownloadJob, message: Optional[str] = None) -> DownloadJob:
    return update_download_job_status(
        session,
        job,
        status="failed",
        sab_status="Failed",
        message=message,
    )


def find_download_job_for_entry(session: Session, entry_name: str) -> Optional[DownloadJob]:
    entry_name = entry_name.strip()
    normalized_entry = entry_name.lower()
    candidates = session.exec(
        select(DownloadJob).where(
            DownloadJob.status.in_(["pending", "queued", "downloading", "processing", "completed"])
        )
    ).all()

    for candidate in candidates:
        compare_targets = [
            candidate.clean_name or "",
            candidate.content_name or "",
            candidate.title or "",
        ]
        for target in compare_targets:
            compare = target.strip().lower()
            if compare and compare == normalized_entry:
                return candidate
            if compare and Path(compare).stem == Path(normalized_entry).stem:
                return candidate
        if candidate.title:
            if Path(candidate.title).stem.lower() == Path(entry_name).stem.lower():
                return candidate
    return None


def mark_download_job_moved(session: Session, job: DownloadJob, destination: Path) -> DownloadJob:
    now = datetime.utcnow()
    job.status = "moved"
    job.sab_status = "Processed"
    job.moved_at = now
    job.updated_at = now
    job.message = f"Moved to {destination}"
    job.clean_name = destination.stem if destination.is_file() else destination.name
    job.staging_path = None
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def list_recent_download_jobs(session: Session, limit: int = 50) -> List[DownloadJob]:
    statement = select(DownloadJob).order_by(DownloadJob.created_at.desc()).limit(limit)
    return list(session.exec(statement))


def list_active_download_jobs(session: Session) -> List[DownloadJob]:
    statement = select(DownloadJob).where(
        DownloadJob.status.in_(["pending", "queued", "downloading", "processing", "completed"])
    )
    return list(session.exec(statement))
