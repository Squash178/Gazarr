from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Provider(SQLModel, table=True):
    """Usenet/Torznab provider configuration."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, min_length=1)
    base_url: str = Field(min_length=1, description="Torznab/Newznab endpoint without trailing slash.")
    api_key: str = Field(min_length=1, description="API key provided by the indexer or Prowlarr.")
    enabled: bool = Field(default=True)
    download_types: str = Field(default="M", description="Comma separated provider categories (eg. M, E).")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Magazine(SQLModel, table=True):
    """Magazine titles we want to track."""

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True, unique=True, min_length=1)
    regex: Optional[str] = Field(default=None, description="Optional custom search term/regex.")
    status: str = Field(default="active", description="active | paused")
    language: str = Field(default="en", description="Language code for issue parsing (en|de).")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class SabnzbdConfig(SQLModel, table=True):
    """Persisted SABnzbd connection details editable from the UI."""

    id: Optional[int] = Field(default=None, primary_key=True)
    base_url: Optional[str] = Field(default=None, description="SABnzbd endpoint, e.g. http://localhost:8080/sabnzbd")
    api_key: Optional[str] = Field(default=None, description="SABnzbd full API key.")
    category: Optional[str] = Field(default=None, description="Optional SABnzbd category to target.")
    priority: Optional[int] = Field(default=None, description="Optional SABnzbd priority (-1..2).")
    timeout: Optional[int] = Field(default=None, description="Override HTTP timeout for SABnzbd requests.")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class DownloadJob(SQLModel, table=True):
    """Tracks SABnzbd jobs and downstream processing stages."""

    id: Optional[int] = Field(default=None, primary_key=True)
    sabnzbd_id: Optional[str] = Field(default=None, index=True, description="SABnzbd NZO identifier.")
    title: Optional[str] = Field(default=None, description="Original release title provided to SABnzbd.")
    magazine_title: Optional[str] = Field(default=None, description="Canonical magazine title provided by Gazarr.")
    link: Optional[str] = Field(default=None, description="Original NZB link used to enqueue the job.")
    content_name: Optional[str] = Field(default=None, description="Filename or folder name reported by SABnzbd.")
    status: str = Field(default="pending", description="High level status (pending, queued, downloading, completed, failed, moved).")
    sab_status: Optional[str] = Field(default=None, description="Raw SABnzbd status string.")
    progress: Optional[float] = Field(default=None, description="Progress percentage 0-100 when available.")
    time_remaining: Optional[str] = Field(default=None, description="Human friendly time remaining reported by SABnzbd.")
    message: Optional[str] = Field(default=None, description="Optional status message or failure reason.")
    clean_name: Optional[str] = Field(default=None, description="Sanitized folder name used for staging/library.")
    thumbnail_path: Optional[str] = Field(default=None, description="Path to generated thumbnail image.")
    staging_path: Optional[str] = Field(default=None, description="Temporary staging path during processing.")
    issue_code: Optional[str] = Field(default=None, description="Parsed issue code (eg. 20250004).")
    issue_label: Optional[str] = Field(default=None, description="Human friendly issue label (eg. July-August 2025).")
    issue_year: Optional[int] = Field(default=None, description="Issue year if determinable.")
    issue_month: Optional[int] = Field(default=None, description="Issue month if determinable.")
    issue_number: Optional[int] = Field(default=None, description="Issue number if determinable.")
    last_seen: Optional[datetime] = Field(default=None, description="Timestamp of last update received from SABnzbd.")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    completed_at: Optional[datetime] = Field(default=None, description="When SABnzbd finished processing.")
    moved_at: Optional[datetime] = Field(default=None, description="When Gazarr moved the download into the library.")
