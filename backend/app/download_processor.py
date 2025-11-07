import logging
import os
import re
import shutil
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

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
    issue_year: Optional[int] = None
    issue_month: Optional[int] = None
    issue_number: Optional[int] = None
    issue_label: Optional[str] = None
    with Session(engine) as session:
        job = session.get(DownloadJob, job_id)
        if not job:
            raise ValueError(f"No download job found for id {job_id}")

        magazine = _lookup_magazine_by_title(session, job.magazine_title)
        if magazine and getattr(magazine, "language", None):
            magazine_language = magazine.language
        issue_year = job.issue_year
        issue_month = job.issue_month
        issue_number = job.issue_number
        issue_label = job.issue_label
        inferred_date = _infer_interval_publication_date(magazine, issue_number)
        if inferred_date:
            if issue_year is None:
                issue_year = inferred_date.year
            if issue_month is None:
                issue_month = inferred_date.month
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
                issue_label=issue_label,
                issue_year=issue_year,
                issue_month=issue_month,
                issue_number=issue_number,
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


def _lookup_magazine_by_title(session: Session, title: Optional[str]) -> Optional[Magazine]:
    if not title:
        return None
    statement = select(Magazine).where(Magazine.title == title)
    return session.exec(statement).first()


def _infer_interval_publication_date(magazine: Optional[Magazine], issue_number: Optional[int]) -> Optional[datetime]:
    if not magazine or issue_number is None:
        return None
    interval = getattr(magazine, "interval_months", None)
    ref_issue = getattr(magazine, "interval_reference_issue", None)
    ref_year = getattr(magazine, "interval_reference_year", None)
    ref_month = getattr(magazine, "interval_reference_month", None)
    if not all([interval, ref_issue, ref_year, ref_month]):
        return None
    if interval <= 0 or ref_month < 1 or ref_month > 12:
        return None
    diff = int(issue_number) - int(ref_issue)
    start_month_index = ref_year * 12 + (ref_month - 1)
    target_month_index = start_month_index + diff * int(interval)
    if target_month_index < 0:
        return None
    year = target_month_index // 12
    month = (target_month_index % 12) + 1
    try:
        return datetime(year, month, 1, tzinfo=timezone.utc)
    except ValueError:
        return None


def _strip_and_apply_metadata(
    pdf_path: Path,
    *,
    clean_title: str,
    series_name: Optional[str],
    series_index: Optional[str],
    language: str,
    issue_label: Optional[str] = None,
    issue_year: Optional[int] = None,
    issue_month: Optional[int] = None,
    issue_number: Optional[int] = None,
) -> None:
    author = series_name or None
    description = issue_label or None
    published_date = _build_issue_publication_date(issue_year, issue_month)
    published_iso = published_date.isoformat() if published_date else None

    # 1) Re-write the PDF to strip existing metadata and set standard Info keys only
    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    # Only set standard PDF Info fields (avoid custom /calibre:* keys)
    info_metadata = {"/Title": clean_title}
    if author:
        info_metadata["/Author"] = author
    if published_iso:
        pdf_timestamp = _format_pdf_timestamp(published_date)
        info_metadata["/CreationDate"] = pdf_timestamp
        info_metadata["/ModDate"] = pdf_timestamp
    writer.add_metadata(info_metadata)

    temp_path = pdf_path.with_suffix(".tmp.pdf")
    with temp_path.open("wb") as temp_file:
        writer.write(temp_file)
    temp_path.replace(pdf_path)

    # 2) Apply XMP metadata (dc:title, dc:language) using PyMuPDF for broad compatibility
    try:
        doc = fitz.open(str(pdf_path))
        # Update standard info title as well (some readers prioritize this field)
        try:
            current_meta = dict(doc.metadata or {})
            current_meta["title"] = clean_title
            if author:
                current_meta["author"] = author
            if published_iso:
                current_meta["creationDate"] = published_iso
                current_meta["modDate"] = published_iso
            doc.set_metadata(current_meta)
        except Exception:
            # Non-fatal; continue to set XMP
            logger.debug("Unable to set PyMuPDF metadata dict for %s", pdf_path)

        lang = (language or "en").strip() or "en"
        title_xml = xml_escape(clean_title, {"'": "&apos;", '"': "&quot;"})
        lang_xml = xml_escape(lang, {"'": "&apos;", '"': "&quot;"})
        author_xml = xml_escape(author, {"'": "&apos;", '"': "&quot;"}) if author else None
        subject_xml = xml_escape(description, {"'": "&apos;", '"': "&quot;"}) if description else None
        published_xml = xml_escape(published_iso, {"'": "&apos;", '"': "&quot;"}) if published_iso else None
        series_xml = xml_escape(series_name, {"'": "&apos;", '"': "&quot;"}) if series_name else None
        series_index_value = _normalise_series_index(
            raw_series_index=series_index,
            issue_year=issue_year,
            issue_month=issue_month,
            issue_number=issue_number,
        )
        series_index_xml = (
            xml_escape(series_index_value, {"'": "&apos;", '"': "&quot;"})
            if series_index_value is not None
            else None
        )

        # Minimal XMP packet with Dublin Core + Calibre extensions when available
        description_lines = [
            "      <dc:title>",
            "        <rdf:Alt>",
            f"          <rdf:li xml:lang='x-default'>{title_xml}</rdf:li>",
            "        </rdf:Alt>",
            "      </dc:title>",
            "      <dc:language>",
            "        <rdf:Bag>",
            f"          <rdf:li>{lang_xml}</rdf:li>",
            "        </rdf:Bag>",
            "      </dc:language>",
        ]
        if author_xml:
            description_lines.extend(
                [
                    "      <dc:creator>",
                    "        <rdf:Seq>",
                    f"          <rdf:li>{author_xml}</rdf:li>",
                    "        </rdf:Seq>",
                    "      </dc:creator>",
                    f"      <pdf:Author>{author_xml}</pdf:Author>",
                ]
            )
        if subject_xml:
            description_lines.extend(
                [
                    "      <dc:description>",
                    "        <rdf:Alt>",
                    f"          <rdf:li xml:lang='x-default'>{subject_xml}</rdf:li>",
                    "        </rdf:Alt>",
                    "      </dc:description>",
                ]
            )
        if published_xml:
            description_lines.extend(
                [
                    "      <dc:date>",
                    "        <rdf:Seq>",
                    f"          <rdf:li>{published_xml}</rdf:li>",
                    "        </rdf:Seq>",
                    "      </dc:date>",
                ]
            )
        if series_xml or series_index_xml:
            description_lines.append("      <calibre:series rdf:parseType='Resource'>")
            description_lines.append(f"        <rdf:value>{series_xml or ''}</rdf:value>")
            if series_index_xml:
                description_lines.append(f"        <calibreSI:series_index>{series_index_xml}</calibreSI:series_index>")
            description_lines.append("      </calibre:series>")

        description_block = "\n".join(description_lines)

        xmp_packet = f"""
<?xpacket begin='\ufeff' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/'>
  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#' xmlns:dc='http://purl.org/dc/elements/1.1/' xmlns:pdf='http://ns.adobe.com/pdf/1.3/' xmlns:calibre='http://calibre-ebook.com/xmp-namespace' xmlns:calibreSI='http://calibre-ebook.com/xmp-namespace/seriesIndex'>
    <rdf:Description rdf:about=''>
{description_block}
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end='w'?>"""
        doc.set_xml_metadata(xmp_packet)
        doc.saveIncr()
        doc.close()
    except Exception:
        # XMP application is best-effort; keep the PDF even if XMP fails
        logger.exception("Failed applying XMP metadata to %s", pdf_path)


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


def _build_issue_publication_date(issue_year: Optional[int], issue_month: Optional[int]) -> Optional[datetime]:
    if issue_year is None:
        return None
    month = 1
    if issue_month and 1 <= int(issue_month) <= 12:
        month = int(issue_month)
    try:
        # Use midday UTC to keep the calendar date stable across timezones.
        return datetime(issue_year, month, 1, 12, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def _format_pdf_timestamp(value: Optional[datetime]) -> str:
    assert value is not None
    utc_value = value.astimezone(timezone.utc)
    return utc_value.strftime("D:%Y%m%d%H%M%S+00'00'")


def _derive_issue_artifacts(job: DownloadJob) -> Tuple[str, str, str, str, Optional[str]]:
    preferred_magazine = _normalize_spaces(job.magazine_title) if job.magazine_title else None
    fallback_magazine = _normalize_spaces(job.title or job.content_name or "Magazine")
    magazine = preferred_magazine or fallback_magazine
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

    series_name = preferred_magazine or magazine
    return magazine_folder, sanitized_name, metadata_title, series_name, series_index


def _normalise_series_index(
    *,
    raw_series_index: Optional[str],
    issue_year: Optional[int],
    issue_month: Optional[int],
    issue_number: Optional[int],
) -> Optional[str]:
    def _format_decimal(value: Decimal) -> str:
        try:
            quantized = value.quantize(Decimal("0.01"))
        except InvalidOperation:
            quantized = value
        text = format(quantized.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    if issue_number is not None:
        return _format_decimal(Decimal(issue_number))

    if issue_year is not None:
        if issue_month is not None:
            try:
                combined = Decimal(issue_year) + (Decimal(issue_month) / Decimal(100))
                return _format_decimal(combined)
            except InvalidOperation:
                pass
        return str(issue_year)

    if raw_series_index:
        cleaned = raw_series_index.strip()
        if cleaned.isdigit():
            if len(cleaned) >= 6:
                year_part = cleaned[:4]
                month_part = cleaned[4:6]
                try:
                    combined = Decimal(year_part) + (Decimal(month_part) / Decimal(100))
                    return _format_decimal(combined)
                except InvalidOperation:
                    pass
            try:
                return _format_decimal(Decimal(cleaned))
            except InvalidOperation:
                return cleaned
        return cleaned or None

    return None


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
