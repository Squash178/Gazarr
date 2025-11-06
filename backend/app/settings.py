from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="GAZARR_", case_sensitive=False)

    app_name: str = "Gazarr"
    database_url: str = Field(default="sqlite:///data/app.db")
    torznab_timeout: int = Field(default=15, description="HTTP timeout for Torznab requests (seconds).")
    torznab_max_age_days: Optional[int] = Field(default=None, description="Discard results older than N days.")
    sabnzbd_url: Optional[HttpUrl] = Field(default=None, description="Base URL for SABnzbd (eg. http://localhost:8080/sabnzbd)")
    sabnzbd_api_key: Optional[str] = Field(default=None, description="SABnzbd API key.")
    sabnzbd_category: Optional[str] = Field(default=None, description="Optional SABnzbd category to use.")
    sabnzbd_priority: Optional[int] = Field(default=None, description="Optional SABnzbd priority (-1,0,1,2).")
    sabnzbd_timeout: int = Field(default=10, description="HTTP timeout for SABnzbd requests (seconds).")
    downloads_dir: Optional[Path] = Field(default=Path("/downloads"), description="Directory to monitor for completed SABnzbd downloads.")
    library_dir: Optional[Path] = Field(default=Path("/library"), description="Destination for processed downloads.")
    staging_dir: Optional[Path] = Field(default=Path("/staging"), description="Temporary processing area before moving downloads into the library.")
    covers_dir: Optional[Path] = Field(default=Path("data/covers"), description="Directory for generated cover thumbnails.")
    downloads_poll_interval: float = Field(default=10.0, description="Polling interval for the download monitor (seconds).")
    downloads_settle_seconds: float = Field(default=30.0, description="Time without changes before a download is considered complete.")
    download_tracker_poll_interval: float = Field(default=10.0, description="Polling interval for SABnzbd download status updates (seconds).")
    download_tracker_history_limit: int = Field(default=50, description="Number of SABnzbd history entries to inspect per poll.")

    def ensure_sqlite_directory(self) -> None:
        if self.database_url.startswith("sqlite"):
            db_path = self.database_url.split("///")[-1]
            path = Path(db_path).expanduser()
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_library_directory(self) -> None:
        for folder in (self.library_dir, self.staging_dir, self.covers_dir):
            if folder:
                path = folder.expanduser()
                path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_sqlite_directory()
    settings.ensure_library_directory()
    return settings
