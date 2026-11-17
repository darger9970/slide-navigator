import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import stat
import subprocess
import threading
from contextlib import nullcontext
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Sequence

from fastapi import UploadFile
from pptx import Presentation
from pptx.exc import PackageNotFoundError

from app.config import Settings
from app.database import connect_database
from app.library_tags import assigned_tags, filter_tags, replace_tags, tag_filter, generic_tag_filter, categories
from app.presentation_metadata import imported_metadata, metadata_states
from app.rendering import libreoffice_executable

logger = logging.getLogger(__name__)
SUPPORTED_PRESENTATION_EXTENSIONS = {".ppt", ".pptx"}
UPLOAD_CHUNK_SIZE = 1024 * 1024
ACTIVE_IMPORTS = set()
IMPORT_GUARD = threading.Lock()


class IngestionError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_progress(settings, job_id, stage, processed=0, total=None, item_id=None):
    with connect_database(settings.database_path) as connection:
        connection.execute(
            """UPDATE import_jobs SET stage = ?, current_slides_processed = ?,
               current_slides_total = ?, current_item_id = COALESCE(?, current_item_id)
               WHERE id = ?""", (stage, processed, total, item_id, job_id),
        )


def display_presentation_name(name: str | None, filename: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        return filename
    try:
        uuid.UUID(candidate)
    except ValueError:
        return candidate
    return filename


def _normalize_extracted_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n")


def _logical_path(raw_path: str) -> str:
    if "\x00" in raw_path:
        raise IngestionError("The source path contains an invalid character.")
    normalized = raw_path.replace("\\", "/")
    windows_path = PureWindowsPath(normalized)
    path = PurePosixPath(normalized)
    if path.is_absolute() or windows_path.drive or any(
        part == ".." for part in path.parts
    ):
        raise IngestionError("The source path is unsafe.")
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        raise IngestionError("The source filename is empty.")
    return "/".join(parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(UPLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _insert_job_item(
    settings: Settings,
    job_id: str,
    *,
    source_name: str,
    logical_source_path: str,
    staging_path: Path | None,
    source_modified_at: str | None,
    status: str = "queued",
    db=None,
) -> str:
    item_id = str(uuid.uuid4())
    with (nullcontext(db) if db is not None else connect_database(settings.database_path)) as connection:
        connection.execute(
            """
            INSERT INTO import_job_items (
                id, job_id, source_name, logical_source_path, staging_path,
                source_modified_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                job_id,
                source_name,
                logical_source_path,
                str(staging_path) if staging_path else None,
                source_modified_at,
                status,
                _now(),
            ),
        )
        connection.execute(
            "UPDATE import_jobs SET total_items = total_items + 1 WHERE id = ?",
            (job_id,),
        )
    return item_id


def _finish_item(
    settings: Settings,
    job_id: str,
    item_id: str,
    status: str,
    *,
    message: str | None = None,
    deck_id: str | None = None,
    slide_count: int = 0,
    db=None,
) -> None:
    increments = {
        "imported_count": int(status == "imported"),
        "duplicate_count": int(status == "duplicate"),
        "failed_count": int(status == "failed"),
        "slide_count": slide_count if status == "imported" else 0,
    }
    with (nullcontext(db) if db is not None else connect_database(settings.database_path)) as connection:
        changed = connection.execute(
            """
            UPDATE import_job_items
            SET status = ?, message = ?, deck_id = ?, slide_count = ?, staging_path = NULL
            WHERE id = ? AND status IN ('queued','processing')
            """,
            (status, message, deck_id, slide_count, item_id),
        )
        if not changed.rowcount:return
        connection.execute(
            """
            UPDATE import_jobs
            SET processed_items = processed_items + 1,
                imported_count = imported_count + ?,
                duplicate_count = duplicate_count + ?,
                failed_count = failed_count + ?,
                slide_count = slide_count + ?
            WHERE id = ?
            """,
            (
                increments["imported_count"],
                increments["duplicate_count"],
                increments["failed_count"],
                increments["slide_count"],
                job_id,
            ),
        )


async def stage_browser_uploads(
    settings: Settings,
    uploads: Sequence[UploadFile],
    file_metadata: list[dict[str, Any]],
    storage_mode: str,
) -> str:
    job_id = str(uuid.uuid4())
    stage_dir = settings.staging_dir / job_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    created_at = _now()
    with connect_database(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO import_jobs (id, status, storage_mode, total_items, created_at)
            VALUES (?, 'queued', ?, 0, ?)
            """,
            (job_id, storage_mode, created_at),
        )

    for index, upload in enumerate(uploads):
        raw_name = upload.filename or f"unnamed-{index + 1}"
        try:
            logical_path = _logical_path(raw_name)
        except IngestionError as error:
            logical_path = PurePosixPath(raw_name.replace("\\", "/")).name or "invalid-file"
            item_id = _insert_job_item(
                settings,
                job_id,
                source_name=logical_path,
                logical_source_path=logical_path,
                staging_path=None,
                source_modified_at=None,
                status="failed",
            )
            _finish_item(settings, job_id, item_id, "failed", message=str(error))
            await upload.close()
            continue

        extension = Path(logical_path).suffix.lower()
        staged_path = stage_dir / f"{uuid.uuid4().hex}{extension}"
        metadata = file_metadata[index] if index < len(file_metadata) else {}
        modified_ms = metadata.get("last_modified")
        modified_at = None
        if isinstance(modified_ms, (int, float)) and modified_ms > 0:
            modified_at = datetime.fromtimestamp(
                modified_ms / 1000, tz=timezone.utc
            ).isoformat()
        source_name = PurePosixPath(logical_path).name
        item_id = _insert_job_item(
            settings,
            job_id,
            source_name=source_name,
            logical_source_path=logical_path,
            staging_path=staged_path,
            source_modified_at=modified_at,
        )
        byte_count = 0
        try:
            with staged_path.open("wb") as target:
                while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
                    byte_count += len(chunk)
                    if byte_count > settings.max_upload_file_bytes:
                        raise IngestionError(
                            f"File exceeds the configured upload limit of "
                            f"{settings.max_upload_file_bytes} bytes."
                        )
                    target.write(chunk)
            if byte_count == 0:
                raise IngestionError("The uploaded file is empty.")
        except Exception as error:
            staged_path.unlink(missing_ok=True)
            message = (
                str(error)
                if isinstance(error, IngestionError)
                else f"Could not stage uploaded file: {error}"
            )
            _finish_item(settings, job_id, item_id, "failed", message=message)
        finally:
            await upload.close()
    return job_id


def _validate_archive(
    archive_path: Path, settings: Settings
) -> list[zipfile.ZipInfo]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise IngestionError("The ZIP archive is corrupted or unreadable.") from error
    files = [member for member in members if not member.is_dir()]
    if len(files) > settings.max_archive_files:
        raise IngestionError(
            f"The archive contains more than {settings.max_archive_files} files."
        )
    total_size = 0
    for member in members:
        _logical_path(member.filename.rstrip("/"))
        unix_mode = member.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise IngestionError("The archive contains a symbolic link.")
        if member.flag_bits & 0x1:
            raise IngestionError("Encrypted ZIP entries are not supported.")
        if member.is_dir():
            continue
        if member.file_size > settings.max_archive_member_bytes:
            raise IngestionError(
                f"Archive member '{PurePosixPath(member.filename).name}' exceeds "
                "the configured per-file extraction limit."
            )
        total_size += member.file_size
        if total_size > settings.max_archive_total_bytes:
            raise IngestionError(
                "The archive exceeds the configured total extraction limit."
            )
    return files


def _extract_archive_presentations(
    settings: Settings,
    job_id: str,
    archive_item: sqlite3.Row,
) -> list[str]:
    archive_path = Path(archive_item["staging_path"])
    job_dir = settings.staging_dir / job_id
    extracted: list[tuple[str, str, Path, str | None]] = []
    skipped_members = []
    try:
        members = _validate_archive(archive_path, settings)
        total_written = 0
        with zipfile.ZipFile(archive_path) as archive:
            for member in members:
                logical_member = _logical_path(member.filename)
                if Path(logical_member).suffix.lower() not in SUPPORTED_PRESENTATION_EXTENSIONS:
                    skipped_members.append(logical_member)
                    continue
                staged = job_dir / f"{uuid.uuid4().hex}{Path(logical_member).suffix.lower()}"
                member_written = 0
                with archive.open(member) as source, staged.open("wb") as target:
                    while chunk := source.read(UPLOAD_CHUNK_SIZE):
                        member_written += len(chunk)
                        total_written += len(chunk)
                        if member_written > settings.max_archive_member_bytes:
                            raise IngestionError(
                                "An archive member exceeded its extraction limit."
                            )
                        if total_written > settings.max_archive_total_bytes:
                            raise IngestionError(
                                "The archive exceeded its extraction limit."
                            )
                        target.write(chunk)
                if member_written != member.file_size:
                    raise IngestionError("An archive member has an invalid size.")
                member_date = datetime(*member.date_time, tzinfo=timezone.utc).isoformat()
                logical_source = (
                    f"{archive_item['logical_source_path']}!/{logical_member}"
                )
                extracted.append(
                    (
                        PurePosixPath(logical_member).name,
                        logical_source,
                        staged,
                        member_date,
                    )
                )
        if not extracted:
            raise IngestionError(
                "The ZIP archive contains no supported .ppt or .pptx presentations."
            )
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        for _, _, staged, _ in extracted:
            staged.unlink(missing_ok=True)
        if isinstance(error, IngestionError):
            raise
        raise IngestionError("A ZIP archive member is corrupted or unreadable.") from error
    except IngestionError:
        for _, _, staged, _ in extracted:
            staged.unlink(missing_ok=True)
        raise

    item_ids: list[str] = []
    # Publish the whole archive discovery and its terminal container together.
    # A crash cannot leave half the child list published and expand it twice.
    with connect_database(settings.database_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        for source_name, logical_source, staged, member_date in extracted:
            item_ids.append(_insert_job_item(settings,job_id,source_name=source_name,
                logical_source_path=logical_source,staging_path=staged,
                source_modified_at=member_date,db=connection))
        for logical_member in skipped_members:
            skipped_id = _insert_job_item(settings, job_id,
                source_name=PurePosixPath(logical_member).name,
                logical_source_path=f"{archive_item['logical_source_path']}!/{logical_member}",
                staging_path=None, source_modified_at=None,db=connection)
            _finish_item(settings, job_id, skipped_id, "skipped", message="Not a supported PowerPoint presentation; not extracted.",db=connection)
        _finish_item(settings,job_id,archive_item['id'],'skipped',message=f'Expanded archive; found {len(item_ids)} presentations.',db=connection)
    return item_ids


def _libreoffice_executable() -> str | None:
    return libreoffice_executable()


def libreoffice_available() -> bool:
    return _libreoffice_executable() is not None


def _run_libreoffice_worker(
    settings: Settings, action: str, source: Path, output: Path
) -> dict[str, Any]:
    if not libreoffice_available():
        raise IngestionError(
            "LibreOffice is unavailable. Install LibreOffice Impress to convert "
            "legacy PPT files or generate slide thumbnails."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "app.libreoffice_worker",
        "--action",
        action,
        "--source",
        str(source),
        "--output",
        str(output),
    ]
    options: dict[str, Any] = {
        "cwd": Path(__file__).resolve().parents[1],
        "capture_output": True,
        "text": True,
        "timeout": settings.worker_timeout_seconds,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, **options)
    except subprocess.TimeoutExpired as error:
        raise IngestionError(
            f"LibreOffice processing exceeded the "
            f"{settings.worker_timeout_seconds}-second time limit."
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else "LibreOffice processing failed."
        raise IngestionError(message[:500])
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise IngestionError("LibreOffice processing returned invalid output.") from error
    if not isinstance(response, dict):
        raise IngestionError("LibreOffice processing returned invalid output.")
    return response


def _frame_text(frame: Any) -> list[str]:
    collected: list[str] = []
    for paragraph in frame.paragraphs:
        text = _normalize_extracted_text(paragraph.text)
        collected.extend(line.strip() for line in text.splitlines() if line.strip())
    return collected


def _shape_text(shape: Any) -> list[str]:
    collected: list[str] = []
    if getattr(shape, "has_text_frame", False):
        collected.extend(_frame_text(shape.text_frame))
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                collected.extend(_frame_text(cell.text_frame))
    child_shapes = getattr(shape, "shapes", None)
    if child_shapes is not None:
        for child in child_shapes:
            collected.extend(_shape_text(child))
    return collected


def _heading_candidate(
    shape: Any, slide_height: int
) -> tuple[tuple[float, int, float, int], str] | None:
    if not getattr(shape, "has_text_frame", False) or getattr(
        shape, "has_table", False
    ):
        return None
    paragraphs = [
        " ".join(_normalize_extracted_text(paragraph.text).split())
        for paragraph in shape.text_frame.paragraphs
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    if not paragraphs or len(paragraphs) > 2:
        return None
    candidate = " ".join(paragraphs)
    if (
        len(candidate) > 120
        or len(candidate.split()) > 18
        or candidate[-1:] in {".", ";", ","}
        or candidate.startswith(("•", "‣", "▪", "-", "*"))
    ):
        return None

    font_sizes: list[float] = []
    bold = False
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size is not None:
                font_sizes.append(float(run.font.size.pt))
            bold = bold or run.font.bold is True
    font_size = max(font_sizes, default=0.0)
    top_ratio = max(0.0, float(shape.top) / slide_height)
    prominent = (
        font_size >= 18
        or (font_size >= 14 and (bold or top_ratio <= 0.22))
        or (top_ratio <= 0.14 and len(candidate) <= 64)
    )
    if top_ratio > 0.42 or not prominent:
        return None
    return (font_size, int(bold), -top_ratio, -len(candidate)), candidate


def _infer_slide_title(slide: Any, slide_height: int) -> str | None:
    candidates = [
        candidate
        for shape in slide.shapes
        if (candidate := _heading_candidate(shape, slide_height)) is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def extract_presentation(
    presentation_path: Path, progress: Callable[[int, int], None] | None = None
) -> dict[str, Any]:
    try:
        presentation = Presentation(str(presentation_path))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, PackageNotFoundError) as error:
        raise IngestionError(
            "The PowerPoint file is corrupted or is not a supported presentation."
        ) from error
    properties = presentation.core_properties
    deck_name = (properties.title or "").strip() or presentation_path.stem
    source_metadata = {}
    for field in ("title", "subject", "author", "keywords", "comments", "category",
                  "last_modified_by", "created", "modified", "revision", "content_status",
                  "language", "version"):
        value = getattr(properties, field, None)
        source_metadata[field] = value.isoformat() if isinstance(value, datetime) else value
    total_slides = len(presentation.slides)
    subtitle = None
    if total_slides:
        for placeholder in presentation.slides[0].placeholders:
            if int(placeholder.placeholder_format.type) == 4 and placeholder.has_text_frame:
                subtitle = placeholder.text.strip() or None
                break
    if progress:
        progress(0, total_slides)
    slides: list[dict[str, str | int]] = []
    for number, slide in enumerate(presentation.slides, start=1):
        text_parts: list[str] = []
        for shape in slide.shapes:
            text_parts.extend(_shape_text(shape))
        notes = ""
        try:
            notes_frame = slide.notes_slide.notes_text_frame
            if notes_frame is not None:
                notes = notes_frame.text.strip()
        except (AttributeError, KeyError, ValueError):
            notes = ""
        slide_title = ""
        title_source = "fallback"
        try:
            title_shape = slide.shapes.title
            if title_shape is not None and title_shape.has_text_frame:
                slide_title = _normalize_extracted_text(
                    title_shape.text_frame.text
                ).strip()
                if slide_title:
                    title_source = "placeholder"
        except (AttributeError, KeyError, ValueError):
            pass
        if not slide_title:
            slide_title = _infer_slide_title(
                slide, int(presentation.slide_height)
            ) or ""
            if slide_title:
                title_source = "inferred"
        if not slide_title:
            slide_title = f"Slide {number} — Untitled"
        slides.append(
            {
                "slide_number": number,
                "internal_slide_id": str(slide.slide_id),
                "title": slide_title,
                "title_source": title_source,
                "text_content": "\n".join(text_parts),
                "speaker_notes": notes,
            }
        )
        if progress:
            progress(number, total_slides)
    return {"name": deck_name, "slides": slides, "source_metadata": {
        "document_properties": source_metadata,
        "slide_content": {"subtitle": subtitle, "subtitle_source": "first_slide_subtitle_placeholder" if subtitle else None},
        "extraction": {"library": "python-pptx", "source_format": presentation_path.suffix.lower()},
    }}


def _infer_title_from_text(text_content: str) -> str | None:
    for line in _normalize_extracted_text(text_content).splitlines():
        candidate = " ".join(line.split())
        if (
            not candidate
            or len(candidate) > 100
            or len(candidate.split()) > 14
            or "|" in candidate
            or candidate[-1:] in {".", ";", ","}
            or candidate.startswith(("•", "‣", "▪", "-", "*"))
        ):
            continue
        return candidate
    return None


def backfill_slide_titles(database_path: Path) -> int:
    with connect_database(database_path) as connection:
        connection.execute(
            """
            UPDATE slides
            SET text_content = replace(
                replace(
                    replace(text_content, char(11), char(10)),
                    char(13),
                    char(10)
                ),
                ' | ',
                char(10)
            )
            WHERE instr(text_content, char(11)) > 0
               OR instr(text_content, char(13)) > 0
               OR instr(text_content, ' | ') > 0
            """
        )
        rows = connection.execute(
            """
            SELECT id, slide_number, title, text_content
            FROM slides WHERE title_source = 'fallback'
            """
        ).fetchall()
        for row in rows:
            existing_title = (row["title"] or "").strip()
            fallback_title = f"Slide {row['slide_number']} — Untitled"
            if existing_title and existing_title != fallback_title:
                title = existing_title
                source = "placeholder"
            else:
                title = _infer_title_from_text(row["text_content"]) or fallback_title
                source = "inferred" if title != fallback_title else "fallback"
            connection.execute(
                "UPDATE slides SET title = ?, title_source = ? WHERE id = ?",
                (title, source, row["id"]),
            )
    return len(rows)


def _find_existing_deck(settings: Settings, checksum: str) -> sqlite3.Row | None:
    with connect_database(settings.database_path) as connection:
        return connection.execute(
            """
            SELECT d.id, d.name, d.slide_count, d.stored_path
            FROM file_checksums c
            JOIN decks d ON d.id = c.deck_id
            WHERE c.sha256 = ?
            """,
            (checksum,),
        ).fetchone()


def _record_source_location(
    settings: Settings, deck_id: str, item: sqlite3.Row
) -> None:
    source_logical_path = item["logical_source_path"]
    source_type = "archive_member" if "!/" in source_logical_path else (
        "browser_folder_upload" if "/" in source_logical_path else "browser_upload"
    )
    with connect_database(settings.database_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO source_locations (
                id, deck_id, source_path, logical_path, source_type, imported_at
            ) VALUES (?, ?, NULL, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                deck_id,
                source_logical_path,
                source_type,
                _now(),
            ),
        )


def _insert_deck(
    settings: Settings,
    deck_id: str,
    item: sqlite3.Row,
    checksum: str,
    file_size: int,
    extracted: dict[str, Any],
    imported_path: Path,
    storage_mode: str,
) -> tuple[str, bool, int]:
    imported_at = _now()
    modified_at = item["source_modified_at"]
    source_logical_path = item["logical_source_path"]
    source_type = "archive_member" if "!/" in source_logical_path else (
        "browser_folder_upload" if "/" in source_logical_path else "browser_upload"
    )
    with connect_database(settings.database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT d.id, d.slide_count FROM file_checksums c
            JOIN decks d ON d.id = c.deck_id WHERE c.sha256 = ?
            """,
            (checksum,),
        ).fetchone()
        if existing is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_locations (
                    id, deck_id, source_path, logical_path, source_type, imported_at
                ) VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    existing["id"],
                    source_logical_path,
                    source_type,
                    imported_at,
                ),
            )
            return existing["id"], True, existing["slide_count"]

        connection.execute(
            """
            INSERT INTO decks (
                id, name, original_filename, stored_path, storage_mode, file_size,
                modified_at, imported_at, slide_count, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                deck_id,
                extracted["name"],
                item["source_name"],
                str(imported_path),
                storage_mode,
                file_size,
                modified_at,
                imported_at,
                len(extracted["slides"]),
                json.dumps(extracted.get("source_metadata", {})),
            ),
        )
        connection.execute(
            """UPDATE decks SET author = ?, subject = ?, subtitle = ?, description = ?,
               year = ?, metadata_provenance = ? WHERE id = ?""",
            (extracted.get("author"), extracted.get("subject"), extracted.get("subtitle"),
             extracted.get("description"), extracted.get("year"),
             json.dumps(extracted.get("metadata_provenance", {})), deck_id),
        )
        connection.execute(
            """
            INSERT INTO file_checksums (sha256, deck_id, file_size, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (checksum, deck_id, file_size, imported_at),
        )
        connection.execute(
            """
            INSERT INTO source_locations (
                id, deck_id, source_path, logical_path, source_type, imported_at
            ) VALUES (?, ?, NULL, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                deck_id,
                source_logical_path,
                source_type,
                imported_at,
            ),
        )
        for slide in extracted["slides"]:
            slide_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO slides (
                    id, deck_id, slide_number, internal_slide_id, title,
                    title_source, text_content, speaker_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slide_id,
                    deck_id,
                    slide["slide_number"],
                    slide["internal_slide_id"],
                    slide["title"],
                    slide["title_source"],
                    slide["text_content"],
                    slide["speaker_notes"],
                ),
            )
            connection.execute(
                """
                INSERT INTO slide_renderings (slide_id, status, updated_at)
                VALUES (?, 'pending', ?)
                """,
                (slide_id, imported_at),
            )
        # Record ownership in the same transaction as the new deck. Recovery can
        # resume rendering without misreporting this new deck as a duplicate.
        connection.execute('UPDATE import_job_items SET deck_id=? WHERE id=?',(deck_id,item['id']))
    return deck_id, False, len(extracted["slides"])


def _set_rendering(
    settings: Settings,
    slide_id: str,
    status: str,
    *,
    thumbnail_path: str | None = None,
    error: str | None = None,
) -> None:
    if status in {"failed", "unavailable"} and get_thumbnail_path(settings, slide_id) is not None:
        return  # A failed retry must not discard a valid previously cached preview.
    with connect_database(settings.database_path) as connection:
        connection.execute(
            """
            UPDATE slide_renderings
            SET status = ?, thumbnail_path = ?, error = ?, updated_at = ?
            WHERE slide_id = ?
            """,
            (status, thumbnail_path, error, _now(), slide_id),
        )


def _render_deck(
    settings: Settings,
    job_id: str,
    deck_id: str,
    presentation_path: Path,
    progress=None,
) -> None:
    report = progress or (lambda stage, processed=0, total=None: _job_progress(settings, job_id, stage, processed, total))
    with connect_database(settings.database_path) as connection:
        slides = connection.execute(
            "SELECT id, slide_number FROM slides WHERE deck_id = ? ORDER BY slide_number",
            (deck_id,),
        ).fetchall()
    if not settings.rendering_enabled:
        error = "Slide rendering is disabled by configuration."
        for slide in slides:
            _set_rendering(settings, slide["id"], "unavailable", error=error)
        return False
    if not libreoffice_available():
        error = (
            "No supported local renderer is available. Install LibreOffice Impress "
            "to enable local slide previews."
        )
        for slide in slides:
            _set_rendering(settings, slide["id"], "unavailable", error=error)
        return False
    report("generating_thumbnails")
    output_dir = settings.staging_dir / job_id / "renders" / deck_id
    try:
        response = _run_libreoffice_worker(
            settings, "render", presentation_path, output_dir
        )
        rendered = {
            entry["slide_number"]: entry
            for entry in response.get("slides", [])
            if isinstance(entry, dict) and isinstance(entry.get("slide_number"), int)
        }
        if any(slide["slide_number"] not in rendered for slide in slides):
            raise IngestionError(
                "LibreOffice did not return every indexed slide while rendering."
            )
        thumbnail_dir = settings.thumbnails_dir / deck_id
        thumbnail_dir.mkdir(parents=True, exist_ok=True)
        successful = True
        report("saving_thumbnails", 0, len(slides))
        for index, slide in enumerate(slides, start=1):
            result = rendered.get(slide["slide_number"], {})
            source = Path(result["path"]) if result.get("path") else None
            if result.get("error") or source is None or not source.is_file():
                successful = False
                _set_rendering(
                    settings,
                    slide["id"],
                    "failed",
                    error=str(result.get("error") or "LibreOffice did not create a thumbnail."),
                )
                report("saving_thumbnails", index, len(slides))
                continue
            filename = f"slide-{slide['slide_number']:05}.png"
            target = thumbnail_dir / filename
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            relative_path = target.relative_to(settings.thumbnails_dir).as_posix()
            _set_rendering(
                settings,
                slide["id"],
                "available",
                thumbnail_path=relative_path,
            )
            report("saving_thumbnails", index, len(slides))
        return successful
    except (IngestionError, OSError, subprocess.SubprocessError) as error:
        logger.warning("Slide rendering failed for deck %s: %s", deck_id, error)
        for slide in slides:
            _set_rendering(settings, slide["id"], "failed", error=str(error)[:500])
        return False


def _process_presentation_item(
    settings: Settings, job_id: str, item: sqlite3.Row, storage_mode: str
) -> tuple[str, int, str | None]:
    if item['deck_id']:
        with connect_database(settings.database_path) as connection:
            existing=connection.execute('SELECT stored_path,slide_count FROM decks WHERE id=?',(item['deck_id'],)).fetchone()
        if existing:
            _job_progress(settings,job_id,'resuming_thumbnails',item_id=item['id'])
            _render_deck(settings,job_id,item['deck_id'],Path(existing['stored_path']))
            return item['deck_id'],existing['slide_count'],None
    source_file = Path(item["staging_path"])
    extension = source_file.suffix.lower()
    if extension not in SUPPORTED_PRESENTATION_EXTENSIONS:
        raise IngestionError("Only .pptx, .ppt, and ZIP files are supported.")
    if storage_mode == "reference":
        raise IngestionError(
            "Reference mode is not available for browser uploads because the "
            "browser does not provide a trustworthy absolute Windows path. "
            "Choose managed copy; local-path reference support is deferred."
        )
    file_size = source_file.stat().st_size
    _job_progress(settings, job_id, "checking_duplicate", item_id=item["id"])
    checksum = _sha256(source_file)
    existing = _find_existing_deck(settings, checksum)
    if existing is not None:
        _record_source_location(settings, existing["id"], item)
        return existing["id"], existing["slide_count"], "duplicate"

    extraction_path = source_file
    converted_path: Path | None = None
    if extension == ".ppt":
        _job_progress(settings, job_id, "converting_presentation")
        converted_path = source_file.with_suffix(".converted.pptx")
        _run_libreoffice_worker(settings, "convert", source_file, converted_path)
        extraction_path = converted_path
    else:
        _validate_pptx_archive(source_file, settings)
    _job_progress(settings, job_id, "extracting_metadata")
    extracted = extract_presentation(extraction_path, progress=lambda processed, total:
        _job_progress(settings, job_id, "extracting_slides", processed, total))
    values, provenance = imported_metadata(extracted, item["source_name"], item["source_modified_at"])
    extracted.update(values)
    extracted["metadata_provenance"] = provenance
    extracted["source_metadata"]["extraction"]["source_format"] = extension
    extracted["source_metadata"]["extraction"]["converted_with"] = "LibreOffice" if extension == ".ppt" else None
    deck_id = str(uuid.uuid4())
    destination_dir = settings.imports_dir / deck_id
    destination_dir.mkdir(parents=True, exist_ok=False)
    imported_path = destination_dir / PurePosixPath(item["source_name"]).name
    try:
        _job_progress(settings, job_id, "saving_metadata", len(extracted["slides"]), len(extracted["slides"]))
        shutil.copy2(source_file, imported_path)
        stored_deck_id, duplicate, existing_slide_count = _insert_deck(
            settings,
            deck_id,
            item,
            checksum,
            file_size,
            extracted,
            imported_path,
            storage_mode,
        )
        if duplicate:
            shutil.rmtree(destination_dir)
            return stored_deck_id, existing_slide_count, "duplicate"
    except Exception:
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        raise
    _render_deck(settings, job_id, stored_deck_id, imported_path)
    _job_progress(settings, job_id, "finalising_deck", len(extracted["slides"]), len(extracted["slides"]))
    return stored_deck_id, len(extracted["slides"]), None


def _validate_pptx_archive(path: Path, settings: Settings) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise IngestionError(
            "The PPTX file is corrupted or is not a valid PowerPoint package."
        ) from error
    files = [member for member in members if not member.is_dir()]
    if len(files) > settings.max_archive_files:
        raise IngestionError("The PPTX package contains too many internal files.")
    total_size = 0
    for member in files:
        if member.file_size > settings.max_archive_member_bytes:
            raise IngestionError("A PPTX package part exceeds the configured size limit.")
        total_size += member.file_size
        if total_size > settings.max_archive_total_bytes:
            raise IngestionError("The PPTX package exceeds the configured size limit.")


def recover_imports(settings):
    recovered=[]
    with IMPORT_GUARD,connect_database(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT id FROM import_jobs WHERE status IN ('queued','processing')").fetchall():
            key=(str(settings.database_path.resolve()),row['id'])
            if key in ACTIVE_IMPORTS:continue
            db.execute("UPDATE import_jobs SET status='queued',stage='recovering',completed_at=NULL WHERE id=?",(row['id'],))
            db.execute("UPDATE import_job_items SET status='queued' WHERE job_id=? AND status='processing'",(row['id'],))
            recovered.append(row['id'])
    return recovered


def process_import_job(settings: Settings, job_id: str) -> None:
    key=(str(settings.database_path.resolve()),job_id)
    with IMPORT_GUARD:
        if key in ACTIVE_IMPORTS:return
        ACTIVE_IMPORTS.add(key)
    try:_process_import_job(settings,job_id)
    finally:
        with IMPORT_GUARD:ACTIVE_IMPORTS.discard(key)


def _process_import_job(settings: Settings, job_id: str) -> None:
    with connect_database(settings.database_path) as connection:
        job = connection.execute(
            "SELECT storage_mode FROM import_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            raise IngestionError("The import job does not exist.")
        storage_mode = job["storage_mode"]
        claimed = connection.execute(
            "UPDATE import_jobs SET status = 'processing', started_at = COALESCE(started_at,?), stage = 'discovering_files' WHERE id = ? AND status = 'queued'",
            (_now(), job_id),
        )
        if not claimed.rowcount:
            return  # Reconnecting clients and repeated worker calls must not reprocess jobs.
    try:
        # Discover all archives first: containers are not presentations and do not
        # enter the deck denominator. Unknown totals remain indeterminate.
        with connect_database(settings.database_path) as connection:
            archives = connection.execute("SELECT * FROM import_job_items WHERE job_id = ? AND status = 'queued' AND lower(source_name) LIKE '%.zip' ORDER BY rowid", (job_id,)).fetchall()
        for archive in archives:
            _job_progress(settings, job_id, "discovering_files", item_id=archive["id"])
            try:
                child_ids = _extract_archive_presentations(settings, job_id, archive)
                _finish_item(settings, job_id, archive["id"], "skipped", message=f"Expanded archive; found {len(child_ids)} presentations.")
            except Exception as error:
                _finish_item(settings, job_id, archive["id"], "failed", message=(str(error).strip() or "Archive discovery failed.")[:500])
        with connect_database(settings.database_path) as connection:
            connection.execute("UPDATE import_jobs SET discovery_complete = 1, current_item_id = NULL WHERE id = ?", (job_id,))
        while True:
            with connect_database(settings.database_path) as connection:
                item = connection.execute(
                    """
                    SELECT * FROM import_job_items
                    WHERE job_id = ? AND status = 'queued'
                    ORDER BY created_at, rowid LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()
                if item is not None:
                    connection.execute(
                        "UPDATE import_job_items SET status = 'processing' WHERE id = ?",
                        (item["id"],),
                    )
            if item is None:
                break
            _job_progress(settings, job_id, "extracting_metadata", item_id=item["id"])
            try:
                extension = Path(item["logical_source_path"]).suffix.lower()
                if extension == ".zip":
                    child_ids = _extract_archive_presentations(settings, job_id, item)
                    _finish_item(
                        settings,
                        job_id,
                        item["id"],
                        "skipped",
                        message=f"Expanded archive; found {len(child_ids)} presentations.",
                    )
                    continue
                deck_id, slide_count, duplicate_status = _process_presentation_item(
                    settings, job_id, item, storage_mode
                )
                status = duplicate_status or "imported"
                _finish_item(
                    settings,
                    job_id,
                    item["id"],
                    status,
                    message=(
                        "Exact duplicate; linked to the existing deck."
                        if duplicate_status
                        else None
                    ),
                    deck_id=deck_id,
                    slide_count=slide_count,
                )
            except Exception as error:
                logger.exception(
                    "Import failed for job %s item %s", job_id, item["id"]
                )
                message = (
                    str(error).strip() or "An unexpected error occurred."
                )[:500]
                _finish_item(
                    settings, job_id, item["id"], "failed", message=message
                )
        _job_progress(settings, job_id, "finalising_import")
    except Exception as error:
        logger.exception("Import job %s failed", job_id)
        with connect_database(settings.database_path) as connection:
            connection.execute(
                """
                UPDATE import_jobs
                SET status = 'failed', stage = 'failed', completed_at = ?
                WHERE id = ?
                """,
                (_now(), job_id),
            )
        raise
    finally:
        stage_dir = settings.staging_dir / job_id
        if stage_dir.exists():
            try:
                shutil.rmtree(stage_dir)
            except OSError:
                logger.warning("Could not clean import staging directory %s", stage_dir)

    with connect_database(settings.database_path) as connection:
        job = connection.execute(
            "SELECT failed_count FROM import_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        status = "completed_with_errors" if job["failed_count"] else "completed"
        connection.execute(
            "UPDATE import_jobs SET status = ?, completed_at = ?, stage = 'finished', current_item_id = NULL, current_slides_processed = 0, current_slides_total = NULL WHERE id = ?",
            (status, _now(), job_id),
        )


def _deck_dict(connection: Any, row: Any) -> dict[str, Any]:
    result = dict(row)
    for field in ("source_metadata", "metadata_provenance"):
        result[field] = json.loads(result[field])
    result["year_source"] = result["metadata_provenance"].get("year", {}).get("source", "legacy_unknown")
    result["metadata_states"] = metadata_states(result, "deck")
    result["keywords"] = json.loads(result["keywords"])
    cover = connection.execute("SELECT s.id, r.updated_at FROM slides s LEFT JOIN slide_renderings r ON r.slide_id = s.id WHERE s.deck_id = ? ORDER BY s.slide_number LIMIT 1", (row["id"],)).fetchone()
    result["cover_slide_id"] = cover["id"] if cover else None
    result["cover_thumbnail_version"] = cover["updated_at"] if cover else None
    result["tags"] = assigned_tags(connection, "deck", row["id"])
    result["name"] = display_presentation_name(
        result.get("name"), result["original_filename"]
    )
    result["organisation"] = result.pop("organisation_name", None)
    result.pop("stored_path", None)
    result["source_locations"] = [
        dict(location)
        for location in connection.execute(
            """
            SELECT source_path, logical_path, source_type, imported_at
            FROM source_locations WHERE deck_id = ? ORDER BY imported_at
            """,
            (row["id"],),
        ).fetchall()
    ]
    result["thumbnail_status"] = dict(
        connection.execute(
            """
            SELECT
                COALESCE(SUM(status = 'available'), 0) AS available,
                COALESCE(SUM(status = 'unavailable'), 0) AS unavailable,
                COALESCE(SUM(status = 'failed'), 0) AS failed
            FROM slide_renderings r
            JOIN slides s ON s.id = r.slide_id
            WHERE s.deck_id = ?
            """,
            (row["id"],),
        ).fetchone()
    )
    return result


def list_decks(
    settings: Settings,
    *,
    offset: int = 0,
    limit: int = 50,
    query: str = "",
    organisation_id: str | None = None,
    year: int | None = None,
    sort_by: str = "imported_at",
    sort_order: str = "desc",
    deck_types: list[str] | None = None,
    slide_types: list[str] | None = None,
    tag_ids: list[str] | None = None,
) -> dict[str, Any]:
    pattern = f"%{query.strip()}%"
    terms = ["(? = '' OR d.name LIKE ? OR d.original_filename LIKE ? OR d.description LIKE ? OR o.name LIKE ?)"]
    parameters: list[Any] = [query.strip(), pattern, pattern, pattern, pattern]
    if organisation_id is not None:
        terms.append("d.organisation_id = ?")
        parameters.append(organisation_id)
    if year is not None:
        terms.append("d.year = ?")
        parameters.append(year)
    tag_filter(terms, parameters, "deck", "deck_type", deck_types)
    tag_filter(terms, parameters, "deck", "slide_type", slide_types)
    generic_tag_filter(terms, parameters, "deck", tag_ids, settings.database_path)
    where = "WHERE " + " AND ".join(terms)
    display_name_sql = (
        "CASE WHEN d.name GLOB '????????-????-????-????-????????????' "
        "OR (length(d.name) = 32 AND d.name NOT GLOB '*[^0-9A-Fa-f]*') "
        "THEN d.original_filename ELSE d.name END"
    )
    sort_columns = {
        "name": display_name_sql,
        "original_filename": "d.original_filename",
        "organisation": "o.name",
        "year": "d.year",
        "slide_count": "d.slide_count",
        "imported_at": "d.imported_at",
        "description": "d.description",
    }
    order_column = sort_columns.get(sort_by, "d.imported_at")
    direction = "ASC" if sort_order == "asc" else "DESC"
    with connect_database(settings.database_path) as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM decks d LEFT JOIN organisations o ON o.id = d.organisation_id {where}",
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT d.*, (
                SELECT sl.logical_path FROM source_locations sl
                WHERE sl.deck_id = d.id ORDER BY sl.imported_at LIMIT 1
            ) AS source_logical_path, o.name AS organisation_name
            FROM decks d
            LEFT JOIN organisations o ON o.id = d.organisation_id
            {where}
            ORDER BY {order_column} {direction}, d.id
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        ).fetchall()
        items = [_deck_dict(connection, row) for row in rows]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def get_deck(settings: Settings, deck_id: str) -> dict[str, Any] | None:
    with connect_database(settings.database_path) as connection:
        row = connection.execute(
            """
            SELECT d.*, (
                SELECT sl.logical_path FROM source_locations sl
                WHERE sl.deck_id = d.id ORDER BY sl.imported_at LIMIT 1
            ) AS source_logical_path, o.name AS organisation_name
            FROM decks d
            LEFT JOIN organisations o ON o.id = d.organisation_id
            WHERE d.id = ?
            """,
            (deck_id,),
        ).fetchone()
        return _deck_dict(connection, row) if row is not None else None


def list_filter_options(settings: Settings) -> dict[str, Any]:
    with connect_database(settings.database_path) as connection:
        tags = filter_tags(connection)
        category_options = categories(connection)
        organisations = connection.execute(
            """
            SELECT DISTINCT o.id, o.name
            FROM organisations o JOIN decks d ON d.organisation_id = o.id
            ORDER BY o.name COLLATE NOCASE
            """
        ).fetchall()
        years = connection.execute(
            "SELECT DISTINCT year FROM decks WHERE year IS NOT NULL ORDER BY year DESC"
        ).fetchall()
        decks = connection.execute(
            """
            SELECT id, name, original_filename FROM decks
            ORDER BY
                CASE WHEN name GLOB '????????-????-????-????-????????????'
                    OR (length(name) = 32 AND name NOT GLOB '*[^0-9A-Fa-f]*')
                    THEN original_filename ELSE name END COLLATE NOCASE
            """
        ).fetchall()
    return {
        "tags": tags,
        "categories": category_options,
        "organisations": [dict(row) for row in organisations],
        "years": [row["year"] for row in years],
        "decks": [
            {
                "id": row["id"],
                "name": display_presentation_name(row["name"], row["original_filename"]),
            }
            for row in decks
        ],
    }


def _record_edit(
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
) -> None:
    connection.execute(
        """
        INSERT INTO edit_history (
            id, entity_type, entity_id, field_name, old_value, new_value, edited_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            entity_type,
            entity_id,
            field_name,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            _now(),
        ),
    )


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def update_deck_metadata(
    settings: Settings, deck_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    with connect_database(settings.database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT d.*, o.name AS organisation_name
            FROM decks d
            LEFT JOIN organisations o ON o.id = d.organisation_id
            WHERE d.id = ?
            """,
            (deck_id,),
        ).fetchone()
        if row is None:
            return None

        if "tag_ids" in updates:
            replace_tags(connection, "deck", deck_id, updates["tag_ids"])
        changes: dict[str, Any] = {}
        history: list[tuple[str, Any, Any]] = []
        if "title" in updates:
            title = updates["title"]
            if not isinstance(title, str) or not title.strip():
                raise ValueError("Deck title cannot be empty.")
            title = title.strip()
            if title != row["name"]:
                changes["name"] = title
                history.append(("title", row["name"], title))
        if "description" in updates:
            description = _optional_text(updates["description"])
            if description != row["description"]:
                changes["description"] = description
                changes["description_status"] = "Manually Edited"
                history.append(("description", row["description"], description))
        if "organisation" in updates:
            organisation_name = _optional_text(updates["organisation"])
            organisation_id = None
            if organisation_name:
                existing = connection.execute(
                    "SELECT id, name FROM organisations WHERE name = ? COLLATE NOCASE",
                    (organisation_name,),
                ).fetchone()
                if existing is None:
                    organisation_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO organisations (id, name, created_at) VALUES (?, ?, ?)",
                        (organisation_id, organisation_name, _now()),
                    )
                else:
                    organisation_id = existing["id"]
                    organisation_name = existing["name"]
            if organisation_id != row["organisation_id"]:
                changes["organisation_id"] = organisation_id
                history.append(
                    ("organisation", row["organisation_name"], organisation_name)
                )
        if "year" in updates:
            year = updates["year"]
            if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
                raise ValueError("Year must be an integer or empty.")
            if year != row["year"]:
                changes["year"] = year
                history.append(("year", row["year"], year))

        for field in ("author", "subject", "subtitle", "purpose", "industry"):
            if field in updates:
                value = _optional_text(updates[field])
                if value != row[field]:
                    changes[field] = value
                    history.append((field, row[field], value))
        if "keywords" in updates:
            if updates["keywords"] is None:
                raise ValueError("Keywords must be a list.")
            value = json.dumps(list(dict.fromkeys(word.strip() for word in updates["keywords"] if word.strip())))
            if value != row["keywords"]:
                changes["keywords"] = value
                history.append(("keywords", row["keywords"], value))
        if "review_status" in updates:
            status = updates["review_status"]
            if status not in {"Reviewed", "Not Reviewed"}:
                raise ValueError("Choose Reviewed or Not Reviewed.")
            if status != row["review_status"]:
                changes["review_status"] = status
                changes["reviewed_at"] = _now() if status == "Reviewed" else None
                history.append(("review_status", row["review_status"], status))
        elif history and row["review_status"] == "Reviewed":
            changes["review_status"] = "Not Reviewed"
            changes["reviewed_at"] = None
        if history:
            provenance = json.loads(row["metadata_provenance"])
            for field, _, _ in history:
                if field != "review_status":
                    old = provenance.get(field, {"source": "legacy_unknown"})
                    provenance[field] = {"source": "manual", "edited_at": _now(), "previous": old.get("previous", old)}
            changes["metadata_provenance"] = json.dumps(provenance)

        if changes:
            columns = ", ".join(f"{column} = ?" for column in changes)
            connection.execute(
                f"UPDATE decks SET {columns} WHERE id = ?",
                (*changes.values(), deck_id),
            )
            for field_name, old_value, new_value in history:
                _record_edit(
                    connection, "deck", deck_id, field_name, old_value, new_value
                )
    return get_deck(settings, deck_id)


def list_slides(
    settings: Settings,
    *,
    offset: int = 0,
    limit: int = 50,
    deck_id: str | None = None,
    query: str = "",
    organisation_id: str | None = None,
    year: int | None = None,
    sort_by: str = "slide_number",
    sort_order: str = "asc",
    slide_types: list[str] | None = None,
    topics: list[str] | None = None,
    tag_ids: list[str] | None = None,
) -> dict[str, Any]:
    terms = ["(? IS NULL OR s.deck_id = ?)"]
    parameters: list[Any] = [deck_id, deck_id]
    if organisation_id is not None:
        terms.append("d.organisation_id = ?")
        parameters.append(organisation_id)
    if year is not None:
        terms.append("d.year = ?")
        parameters.append(year)
    if query.strip():
        terms.append(
            "(s.title LIKE ? OR s.text_content LIKE ? OR s.description LIKE ? "
            "OR d.name LIKE ? OR d.original_filename LIKE ?)"
        )
        pattern = f"%{query.strip()}%"
        parameters.extend((pattern, pattern, pattern, pattern, pattern))
    tag_filter(terms, parameters, "slide", "slide_type", slide_types)
    tag_filter(terms, parameters, "slide", "topic", topics)
    generic_tag_filter(terms, parameters, "slide", tag_ids, settings.database_path)
    where = "WHERE " + " AND ".join(terms)
    sort_columns = {
        "slide_number": "s.slide_number",
        "title": "s.title",
        "deck_name": (
            "CASE WHEN d.name GLOB '????????-????-????-????-????????????' "
            "OR (length(d.name) = 32 AND d.name NOT GLOB '*[^0-9A-Fa-f]*') "
            "THEN d.original_filename ELSE d.name END"
        ),
        "imported_at": "d.imported_at",
    }
    order_column = sort_columns.get(sort_by, "s.slide_number")
    direction = "DESC" if sort_order == "desc" else "ASC"
    with connect_database(settings.database_path) as connection:
        total = connection.execute(
            f"""
            SELECT COUNT(*) FROM slides s JOIN decks d ON d.id = s.deck_id {where}
            """,
            parameters,
        ).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT s.id, s.deck_id, d.name AS deck_name, d.original_filename,
                   d.organisation_id, o.name AS organisation, d.year,
                   s.slide_number, s.internal_slide_id, s.title, s.title_source,
                   s.description, s.description_status,
                   substr(s.text_content, 1, 400) AS text_excerpt,
                   r.status AS rendering_status, r.error AS rendering_error, r.updated_at AS rendering_updated_at
            FROM slides s
            JOIN decks d ON d.id = s.deck_id
            LEFT JOIN organisations o ON o.id = d.organisation_id
            LEFT JOIN slide_renderings r ON r.slide_id = s.id
            {where}
            ORDER BY {order_column} {direction}, d.imported_at DESC, s.slide_number, s.id
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
        ).fetchall()
        tags_by_id = {row["id"]: assigned_tags(connection, "slide", row["id"]) for row in rows}
    items = []
    for row in rows:
        item = dict(row)
        item["tags"] = tags_by_id[row["id"]]
        item["deck_name"] = display_presentation_name(
            item["deck_name"], item["original_filename"]
        )
        items.append(item)
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def get_slide(settings: Settings, slide_id: str) -> dict[str, Any] | None:
    with connect_database(settings.database_path) as connection:
        row = connection.execute(
            """
            SELECT s.*, d.name AS deck_name, d.original_filename,
                   d.organisation_id, d.year, o.name AS organisation,
                   r.status AS rendering_status, r.thumbnail_path, r.error AS rendering_error, r.updated_at AS rendering_updated_at
            FROM slides s
            JOIN decks d ON d.id = s.deck_id
            LEFT JOIN organisations o ON o.id = d.organisation_id
            LEFT JOIN slide_renderings r ON r.slide_id = s.id
            WHERE s.id = ?
            """,
            (slide_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tags"] = assigned_tags(connection, "slide", slide_id)
        result["metadata_provenance"] = json.loads(result["metadata_provenance"])
        result["metadata_states"] = metadata_states(result, "slide")
        result["keywords"] = json.loads(result["keywords"])
        result["deck_name"] = display_presentation_name(
            result["deck_name"], result["original_filename"]
        )
        return result


def update_slide_metadata(
    settings: Settings, slide_id: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    with connect_database(settings.database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT title, title_source, description, description_status, metadata_provenance, industry, keywords FROM slides WHERE id = ?",
            (slide_id,),
        ).fetchone()
        if row is None:
            return None

        if "tag_ids" in updates:
            replace_tags(connection, "slide", slide_id, updates["tag_ids"])
        changes: dict[str, Any] = {}
        history: list[tuple[str, Any, Any]] = []
        if "title" in updates:
            title = updates["title"]
            if not isinstance(title, str) or not title.strip():
                raise ValueError("Slide title cannot be empty.")
            title = title.strip()
            if title != row["title"]:
                changes["title"] = title
                changes["title_source"] = "manual"
                history.append(("title", row["title"], title))
        if "description" in updates:
            description = _optional_text(updates["description"])
            if description != row["description"]:
                changes["description"] = description
                changes["description_status"] = "Manually Edited"
                history.append(("description", row["description"], description))
        for field in ("industry", "keywords"):
            if field in updates:
                if field == "keywords" and updates[field] is None:
                    raise ValueError("Keywords must be a list.")
                value = json.dumps(list(dict.fromkeys(word.strip() for word in updates[field] if word.strip()))) if field == "keywords" else _optional_text(updates[field])
                if value != row[field]:
                    changes[field] = value
                    history.append((field, row[field], value))
        if changes:
            provenance = json.loads(row["metadata_provenance"])
            for field, _, _ in history:
                provenance[field] = {"source": "manual", "edited_at": _now()}
            changes["metadata_provenance"] = json.dumps(provenance)
            columns = ", ".join(f"{column} = ?" for column in changes)
            connection.execute(
                f"UPDATE slides SET {columns} WHERE id = ?",
                (*changes.values(), slide_id),
            )
            for field_name, old_value, new_value in history:
                _record_edit(
                    connection, "slide", slide_id, field_name, old_value, new_value
                )
    return get_slide(settings, slide_id)


def get_import_job(settings: Settings, job_id: str, *, item_offset=0, item_limit=100) -> dict[str, Any] | None:
    with connect_database(settings.database_path) as connection:
        job = connection.execute(
            "SELECT * FROM import_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None:
            return None
        items = connection.execute(
            """
            SELECT i.id, i.source_name, i.logical_source_path, i.status, i.message, i.deck_id, i.slide_count,
                   d.review_status
            FROM import_job_items i LEFT JOIN decks d ON d.id = i.deck_id
            WHERE i.job_id = ? ORDER BY i.created_at, i.rowid
            """,
            (job_id,),
        ).fetchall()
        result = dict(job)
        all_items = [dict(item) for item in items]
        result["items"] = all_items[item_offset:item_offset + item_limit]
        for item in result["items"]:
            item["rendering_results"] = [dict(row) for row in connection.execute(
                """SELECT r.status, r.error, COUNT(*) AS slide_count FROM slide_renderings r
                   JOIN slides s ON s.id = r.slide_id WHERE s.deck_id = ? GROUP BY r.status, r.error""",
                (item["deck_id"],),
            )] if item["deck_id"] else []
        result["items_total"] = len(all_items)
        result["item_offset"] = item_offset
        decks = [item for item in all_items if Path(item["source_name"]).suffix.lower() in SUPPORTED_PRESENTATION_EXTENSIONS]
        terminal = result["status"] in {"completed", "completed_with_errors", "failed"}
        processed = sum(item["status"] in {"imported", "duplicate", "skipped", "failed"} for item in decks)
        result["deck_progress"] = {
            "total": len(decks), "processed": processed,
            "successful": sum(item["status"] == "imported" for item in decks),
            "failed": sum(item["status"] == "failed" for item in decks),
            "skipped": sum(item["status"] in {"skipped", "duplicate"} for item in decks),
            "total_known": bool(result["discovery_complete"]),
        }
        result["skipped_count"] = sum(item["status"] == "skipped" and Path(item["source_name"]).suffix.lower() != ".zip" for item in all_items)
        result["archive_error_count"] = sum(item["status"] == "failed" and Path(item["source_name"]).suffix.lower() == ".zip" for item in all_items)
        result["progress_percent"] = (
            math.floor(1000 * processed / len(decks)) / 10 if decks and result["discovery_complete"]
            else 100 if terminal and not decks else None
        )
        current = next((item for item in all_items if item["id"] == result["current_item_id"]), None)
        total = result["current_slides_total"]
        result["current_deck"] = {
            "filename": current["logical_source_path"] if current and Path(current["source_name"]).suffix.lower() in SUPPORTED_PRESENTATION_EXTENSIONS else None,
            "processed": result["current_slides_processed"], "total": total,
            "percent": math.floor(1000 * result["current_slides_processed"] / total) / 10 if total else None,
        }
        result["server_now"] = _now()
        start = result["started_at"]
        end = result["completed_at"] or result["server_now"]
        result["elapsed_seconds"] = max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()) if start else 0
        return result


def list_import_jobs(
    settings: Settings, *, offset: int = 0, limit: int = 20, active_only: bool = False
) -> dict[str, Any]:
    with connect_database(settings.database_path) as connection:
        where = "WHERE status IN ('queued', 'processing')" if active_only else ""
        total = connection.execute(f"SELECT COUNT(*) FROM import_jobs {where}").fetchone()[0]
        jobs = connection.execute(
            f"""
            SELECT * FROM import_jobs
            {where}
            ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return {"items": [dict(job) for job in jobs], "total": total, "offset": offset, "limit": limit}


def get_thumbnail_path(settings: Settings, slide_id: str) -> Path | None:
    with connect_database(settings.database_path) as connection:
        row = connection.execute(
            "SELECT thumbnail_path FROM slide_renderings WHERE slide_id = ?",
            (slide_id,),
        ).fetchone()
    if row is None or not row["thumbnail_path"]:
        return None
    root = settings.thumbnails_dir.resolve()
    path = (root / row["thumbnail_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path
