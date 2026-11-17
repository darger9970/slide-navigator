"""Retry previews without reimporting, changing metadata, or modifying sources."""
import tempfile
import uuid

from fastapi import HTTPException
from pathlib import Path

from app.database import connect_database
from app.ingestion import _now, _render_deck, libreoffice_available, get_thumbnail_path


def create_job(settings, deck_id):
    if not settings.rendering_enabled:
        raise HTTPException(409, "Thumbnail rendering is disabled by configuration.")
    if not libreoffice_available():
        raise HTTPException(503, "LibreOffice Impress is unavailable. Install it or set SLIDE_NAVIGATOR_LIBREOFFICE_PATH, then restart.")
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        deck = db.execute("SELECT stored_path FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if deck is None:
            raise HTTPException(404, "Presentation not found.")
        if not Path(deck["stored_path"]).is_file():
            raise HTTPException(409, "The stored presentation file is unavailable.")
        total = db.execute("SELECT COUNT(*) FROM slides WHERE deck_id = ?", (deck_id,)).fetchone()[0]
        if not total:
            raise HTTPException(409, "This deck has no indexed slides.")
        existing = db.execute("SELECT id, status FROM thumbnail_jobs WHERE deck_id = ? AND status IN ('queued', 'processing')", (deck_id,)).fetchone()
        if existing:
            return {"job_id": existing["id"], "status": existing["status"]}
        job_id = str(uuid.uuid4())
        db.execute("INSERT INTO thumbnail_jobs (id, deck_id, status, total, created_at) VALUES (?, ?, 'queued', ?, ?)", (job_id, deck_id, total, _now()))
        return {"job_id": job_id, "status": "queued"}


def get_job(settings, job_id):
    with connect_database(settings.database_path) as db:
        row = db.execute("SELECT * FROM thumbnail_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Thumbnail job not found.")
        return dict(row)


def run_job(settings, job_id):
    with connect_database(settings.database_path) as db:
        if not db.execute("UPDATE thumbnail_jobs SET status = 'processing' WHERE id = ? AND status = 'queued'", (job_id,)).rowcount:
            return
        row = db.execute("SELECT j.deck_id, d.stored_path FROM thumbnail_jobs j JOIN decks d ON d.id = j.deck_id WHERE j.id = ?", (job_id,)).fetchone()
    def progress(stage, processed=0, total=None):
        with connect_database(settings.database_path) as db:
            db.execute("UPDATE thumbnail_jobs SET stage = ?, processed = ?, total = ? WHERE id = ?", (stage, processed, total, job_id))
    try:
        if row is None:
            return
        # Isolated retry workspace; original PPT/PPTX stays read-only.
        with tempfile.TemporaryDirectory(prefix="thumbnail-retry-", dir=settings.staging_dir) as work:
            # _render_deck appends renders/deck_id beneath staging_dir/job_id.
            workspace_id = Path(work).name
            rendered = _render_deck(settings, workspace_id, row["deck_id"], Path(row["stored_path"]), progress=progress)
        with connect_database(settings.database_path) as db:
            slides = db.execute("SELECT s.id, r.error FROM slides s LEFT JOIN slide_renderings r ON r.slide_id = s.id WHERE s.deck_id = ?", (row["deck_id"],)).fetchall()
        failed = [slide for slide in slides if get_thumbnail_path(settings, slide["id"]) is None]
        error = next((slide["error"] for slide in failed if slide["error"]), "Rendering failed. Existing cached previews were preserved; check the renderer logs.") if failed or not rendered else None
        status = "failed" if failed or not rendered else "completed"
    except Exception as problem:
        status, error = "failed", str(problem)[:2000]
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE thumbnail_jobs SET status = ?, stage = ?, error = ?, completed_at = ? WHERE id = ?", (status, status, error, _now(), job_id))
