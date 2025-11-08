from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, constr


class ProviderBase(BaseModel):
    name: constr(strip_whitespace=True, min_length=1)
    base_url: HttpUrl = Field(description="Full Torznab/Newznab endpoint such as https://prow.example/api")
    api_key: constr(strip_whitespace=True, min_length=1)
    enabled: bool = True
    download_types: constr(strip_whitespace=True, min_length=1) = "M"


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    name: Optional[constr(strip_whitespace=True, min_length=1)] = None
    base_url: Optional[HttpUrl] = None
    api_key: Optional[constr(strip_whitespace=True, min_length=1)] = None
    enabled: Optional[bool] = None
    download_types: Optional[constr(strip_whitespace=True, min_length=1)] = None


class ProviderRead(ProviderBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProviderCategoryBase(BaseModel):
    code: constr(strip_whitespace=True, min_length=1)
    name: constr(strip_whitespace=True, min_length=1)


class ProviderCategoryCreate(ProviderCategoryBase):
    pass


class ProviderCategoryRead(ProviderCategoryBase):
    id: int
    provider_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MagazineBase(BaseModel):
    title: constr(strip_whitespace=True, min_length=1)
    regex: Optional[constr(strip_whitespace=True, min_length=1)] = None
    status: constr(strip_whitespace=True, min_length=1) = "active"
    language: constr(strip_whitespace=True, min_length=2, max_length=5) = "en"
    interval_months: Optional[int] = Field(
        default=None,
        ge=1,
        le=12,
        description="Number of months between issues to infer publish dates.",
    )
    interval_reference_issue: Optional[int] = Field(
        default=None,
        ge=1,
        description="Issue number tied to the reference date.",
    )
    interval_reference_year: Optional[int] = Field(
        default=None,
        ge=1900,
        le=2200,
        description="Year of the reference issue used for inferred dating.",
    )
    interval_reference_month: Optional[int] = Field(
        default=None,
        ge=1,
        le=12,
        description="Month of the reference issue used for inferred dating.",
    )
    auto_download_since_year: Optional[int] = Field(
        default=None,
        ge=1900,
        le=2200,
        description="Auto downloader only considers issues newer than this year.",
    )
    auto_download_since_issue: Optional[int] = Field(
        default=None,
        ge=1,
        le=10000,
        description="Auto downloader only considers issues with a higher number when the year matches.",
    )


class MagazineCreate(MagazineBase):
    pass


class MagazineUpdate(BaseModel):
    title: Optional[constr(strip_whitespace=True, min_length=1)] = None
    regex: Optional[constr(strip_whitespace=True, min_length=1)] = None
    status: Optional[constr(strip_whitespace=True, min_length=1)] = None
    language: Optional[constr(strip_whitespace=True, min_length=2, max_length=5)] = None
    interval_months: Optional[int] = Field(default=None, ge=1, le=12)
    interval_reference_issue: Optional[int] = Field(default=None, ge=1)
    interval_reference_year: Optional[int] = Field(default=None, ge=1900, le=2200)
    interval_reference_month: Optional[int] = Field(default=None, ge=1, le=12)
    auto_download_since_year: Optional[int] = Field(default=None, ge=1900, le=2200)
    auto_download_since_issue: Optional[int] = Field(default=None, ge=1, le=10000)


class MagazineRead(MagazineBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class HealthResponse(BaseModel):
    status: str = "ok"


class SearchRequest(BaseModel):
    titles: Optional[List[str]] = Field(default=None, description="Optional list of titles to search for instead of all active magazines.")


class SearchResult(BaseModel):
    provider: str
    title: str
    link: HttpUrl
    published: Optional[datetime] = None
    size: Optional[int] = None
    categories: List[str] = Field(default_factory=list)
    magazine_title: Optional[str] = None
    issue_code: Optional[str] = None
    issue_label: Optional[str] = None
    issue_year: Optional[int] = None
    issue_month: Optional[int] = None
    issue_day: Optional[int] = None
    issue_number: Optional[int] = None
    issue_volume: Optional[int] = None


class SabnzbdStatus(BaseModel):
    enabled: bool
    base_url: Optional[HttpUrl] = None
    category: Optional[str] = None


class SabnzbdDownloadMetadata(BaseModel):
    magazine_title: Optional[str] = None
    issue_code: Optional[str] = None
    issue_label: Optional[str] = None
    issue_year: Optional[int] = None
    issue_month: Optional[int] = None
    issue_number: Optional[int] = None


class SabnzbdEnqueueRequest(BaseModel):
    link: HttpUrl = Field(description="NZB download URL to forward to SABnzbd.")
    title: Optional[constr(strip_whitespace=True, min_length=1)] = Field(default=None, description="Optional display title in SABnzbd.")
    metadata: Optional[SabnzbdDownloadMetadata] = Field(default=None, description="Optional issue metadata payload from the UI.")


class SabnzbdEnqueueResponse(BaseModel):
    queued: bool = True
    nzo_ids: List[str] = Field(default_factory=list)
    message: str = "Request queued in SABnzbd"


class SabnzbdTestResponse(BaseModel):
    ok: bool = True
    message: str = "Connection successful."


class SabnzbdConfigBase(BaseModel):
    base_url: Optional[HttpUrl] = None
    api_key: Optional[constr(strip_whitespace=True, min_length=1)] = None
    category: Optional[constr(strip_whitespace=True, min_length=1)] = None
    priority: Optional[int] = Field(default=None, ge=-1, le=2)
    timeout: Optional[int] = Field(default=None, ge=1, le=180)


class SabnzbdConfigUpdate(SabnzbdConfigBase):
    pass


class SabnzbdConfigRead(SabnzbdConfigBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AppConfigBase(BaseModel):
    auto_download_enabled: bool = False
    auto_download_interval: float = Field(default=900.0, ge=30, le=86400)
    auto_download_max_results: int = Field(default=1, ge=1, le=10)
    auto_fail_enabled: bool = False
    auto_fail_minutes: float = Field(default=720.0, ge=1, le=10080)
    debug_logging: bool = False


class AppConfigUpdate(BaseModel):
    auto_download_enabled: Optional[bool] = None
    auto_download_interval: Optional[float] = Field(default=None, ge=30, le=86400)
    auto_download_max_results: Optional[int] = Field(default=None, ge=1, le=10)
    auto_fail_enabled: Optional[bool] = None
    auto_fail_minutes: Optional[float] = Field(default=None, ge=1, le=10080)
    debug_logging: Optional[bool] = None


class AppConfigRead(AppConfigBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProviderCategoryOption(BaseModel):
    id: int
    provider_id: int
    provider_name: str
    code: str
    name: str
    selected: bool


class MagazineCategoryUpdate(BaseModel):
    provider_category_ids: List[int] = Field(default_factory=list)


class AutoDownloadScanResponse(BaseModel):
    started: bool = True
    enqueued: int = 0
    message: str


class DownloadQueueEntry(BaseModel):
    name: str
    type: Literal["file", "directory"]
    size: int
    modified: datetime
    ready: bool


class DownloadJobRead(BaseModel):
    id: int
    sabnzbd_id: Optional[str]
    title: Optional[str]
    magazine_title: Optional[str]
    content_name: Optional[str]
    status: str
    sab_status: Optional[str]
    progress: Optional[float] = None
    time_remaining: Optional[str] = None
    message: Optional[str] = None
    last_seen: Optional[datetime] = None
    clean_name: Optional[str] = None
    thumbnail_path: Optional[str] = None
    staging_path: Optional[str] = None
    issue_code: Optional[str] = None
    issue_label: Optional[str] = None
    issue_year: Optional[int] = None
    issue_month: Optional[int] = None
    issue_number: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    moved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DownloadQueueResponse(BaseModel):
    enabled: bool
    entries: List[DownloadQueueEntry] = Field(default_factory=list)
    jobs: List[DownloadJobRead] = Field(default_factory=list)


class DownloadClearResponse(BaseModel):
    cleared: int = Field(default=0, ge=0)
