from typing import Iterator

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from .settings import get_settings


settings = get_settings()
engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_language_column()
    _ensure_download_job_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _ensure_language_column() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE magazine ADD COLUMN language TEXT DEFAULT 'en'")
    except OperationalError:
        # column already exists
        pass


def _ensure_download_job_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    columns = {
        "magazine_title": "TEXT",
        "clean_name": "TEXT",
        "thumbnail_path": "TEXT",
        "staging_path": "TEXT",
        "issue_code": "TEXT",
        "issue_label": "TEXT",
        "issue_year": "INTEGER",
        "issue_month": "INTEGER",
        "issue_number": "INTEGER",
    }
    for column, ddl_type in columns.items():
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE downloadjob ADD COLUMN {column} {ddl_type}")
        except OperationalError:
            continue
