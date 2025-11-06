import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import fitz  # type: ignore[import]
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select

from .database import engine
from .models import DownloadJob, Magazine
from .services import update_download_job_status

logger = logging.getLogger(__name__)


def process_download_entry(
    staging_path: Path,
    target_dir: Path,
    *,
    resolver: Callable[[Path], Path],
    job_id: int,
    cover_dir: Path,
) -> Path:
    """
    Clean a download in staging using the metadata captured when Gazarr
    triggered the download, then move it into the library.

    Returns the final destination path in the library.
    """

    staging_path = staging_path.resolve()
    if not staging_path.exists():
        raise FileNotFoundError(f"Staging path does not exist: {staging_path}")

    magazine_language = "en"
    with Session(engine) as session:
        job = session.get(DownloadJob, job_id)
        if not job:
            raise ValueError(f"No download job found for id {job_id}")

        magazine_language = _resolve_magazine_language(session, job)
        magazine_folder, sanitized_name, metadata_title, series_name, series_index = _derive_issue_artifacts(job)

        update_download_job_status(
            session,
            job,
            status="processing",
            message="Preparing files",
            content_name=job.content_name or staging_path.name,
            clean_name=sanitized_name,
            staging_path=str(staging_path),
        )

    processed_dir = staging_path
    if processed_dir.name != sanitized_name:
        candidate = resolver(processed_dir.with_name(sanitized_name))
        processed_dir = _safe_rename(staging_path, candidate)
        logger.info("Renamed staging folder: %s -> %s", staging_path, processed_dir)

    pdf_files = sorted(processed_dir.rglob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in %s", processed_dir)

    renamed_files = []
    for idx, original_path in enumerate(pdf_files, start=1):
        base_name = sanitized_name if len(pdf_files) == 1 else f"{sanitized_name}_{idx}"
        new_path = original_path.with_name(f"{base_name}{original_path.suffix}")
        if original_path != new_path:
            if new_path.exists() and new_path.is_file():
                new_path.unlink()
            original_path.rename(new_path)
            logger.info("Renamed PDF: %s -> %s", original_path, new_path)
        renamed_files.append(new_path)

    cover_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path: Optional[Path] = None
    for pdf_file in renamed_files:
        try:
            _strip_and_apply_metadata(
                pdf_file,
                clean_title=metadata_title,
                series_name=series_name,
                series_index=series_index,
                language=magazine_language,
            )
            if thumbnail_path is None:
                cover_path = cover_dir / f"{sanitized_name}.jpg"
                thumbnail_path = _generate_thumbnail(pdf_file, cover_path)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed processing PDF metadata for %s", pdf_file)

    with Session(engine) as session:
        job = session.get(DownloadJob, job_id)
        if job:
            update_download_job_status(
                session,
                job,
                message="Processed and ready to import",
                content_name=processed_dir.name,
                clean_name=processed_dir.name,
                staging_path=str(processed_dir),
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
            )

    destination_dir = target_dir / magazine_folder
    destination_dir.mkdir(parents=True, exist_ok=True)
    final_paths: List[Path] = []
    for pdf_file in renamed_files:
        destination_file = destination_dir / pdf_file.name
        if destination_file.exists():
            if destination_file.is_file():
                destination_file.unlink()
            else:
                shutil.rmtree(destination_file)
        shutil.move(str(pdf_file), str(destination_file))
        final_paths.append(destination_file)

    try:
        processed_dir.rmdir()
    except OSError:
        shutil.rmtree(processed_dir, ignore_errors=True)

    final_destination = final_paths[0] if final_paths else destination_dir
    logger.info("Moved processed download into library: %s", final_destination)
    return final_destination


def _safe_rename(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(source), str(destination))
    return destination


def _resolve_magazine_language(session: Session, job: DownloadJob) -> str:
    """
    Calibre expects language metadata for downstream processing, so lookup the magazine preference.
    Defaults to English when no explicit language is stored.
    """
    if job.magazine_title:
        statement = select(Magazine).where(Magazine.title == job.magazine_title)
        magazine = session.exec(statement).first()
        if magazine and getattr(magazine, "language", None):
            return magazine.language
    return "en"


def _strip_and_apply_metadata(
    pdf_path: Path,
    *,
    clean_title: str,
    series_name: Optional[str],
    series_index: Optional[str],
    language: str,
) -> None:
    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    metadata = {"/Title": clean_title}
    if series_name:
        metadata["/calibre:series"] = series_name
    if series_index:
        metadata["/calibre:series_index"] = series_index
    if language:
        metadata["/calibre:language"] = language

    writer.add_metadata(metadata)

    temp_path = pdf_path.with_suffix(".tmp.pdf")
    with temp_path.open("wb") as temp_file:
        writer.write(temp_file)
    temp_path.replace(pdf_path)


def _generate_thumbnail(pdf_path: Path, output_path: Path) -> Optional[Path]:
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Unable to open PDF for thumbnail generation: %s", pdf_path)
        return None

    try:
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=150)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(output_path))
        return output_path
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed creating thumbnail for %s", pdf_path)
        return None
    finally:
        doc.close()


def _derive_issue_artifacts(job: DownloadJob) -> Tuple[str, str, str, str, Optional[str]]:
    magazine = _normalize_spaces(job.magazine_title or job.title or job.content_name or "Magazine")
    label = _normalize_spaces(job.issue_label) if job.issue_label else None
    series_index = _build_series_index(job)
    magazine_folder = _sanitize_filename(magazine) or "Magazine"

    year_text: Optional[str] = None
    if job.issue_year:
        year_text = f"{int(job.issue_year):04d}"
    elif series_index and len(series_index) >= 4:
        year_text = series_index[:4]
    elif job.issue_code:
        digits = re.sub(r"\D+", "", str(job.issue_code))
        if len(digits) >= 4:
            year_text = digits[:4]

    issue_token: Optional[str] = None
    if job.issue_number is not None:
        issue_token = f"{int(job.issue_number):02d}"
    elif job.issue_month:
        issue_token = f"{int(job.issue_month):02d}"
    elif series_index and len(series_index) >= 6:
        issue_token = series_index[4:6]
    elif job.issue_code:
        digits = re.sub(r"\D+", "", str(job.issue_code))
        if len(digits) >= 6:
            issue_token = digits[4:6]

    file_components = [magazine]
    if issue_token:
        file_components.append(issue_token)
    if year_text:
        file_components.append(year_text)
    elif series_index and len(series_index) >= 4:
        file_components.append(series_index[:4])
    elif label:
        file_components.append(label)

    file_label = _normalize_spaces(" ".join(filter(None, file_components))) or magazine
    sanitized_name = _sanitize_filename(file_label)
    metadata_title = file_label

    series_name = magazine
    return magazine_folder, sanitized_name, metadata_title, series_name, series_index


def _build_series_index(job: DownloadJob) -> Optional[str]:
    if job.issue_code:
        digits = re.sub(r"\D+", "", str(job.issue_code))
        if len(digits) >= 8:
            year = digits[:4]
            mid = digits[4:6]
            tail = digits[6:8]
            if mid and mid != "00":
                return year + mid
            if tail and tail != "00":
                return year + tail
            return digits[:6]
        if len(digits) >= 6:
            return digits[:6]
    if job.issue_year and job.issue_month:
        return f"{int(job.issue_year):04d}{int(job.issue_month):02d}"
    if job.issue_year and job.issue_number is not None:
        return f"{int(job.issue_year):04d}{int(job.issue_number):02d}"
    return None


def _sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^\w\s\-\.]", " ", name, flags=re.UNICODE)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized.strip(" .-_")
    return sanitized or "download"


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
