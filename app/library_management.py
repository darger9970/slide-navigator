"""Transactional library operations. Presentation files are never modified."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.database import connect_database
from app.library_tags import assigned_tags, replace_tags


def deletion(settings, kind, ids, *, execute=False, expected_slide_count=None):
    table = "decks" if kind == "decks" else "slides"
    placeholders = ",".join("?" for _ in ids)
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(f"SELECT id FROM {table} WHERE id IN ({placeholders})", ids).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(409, "Selection changed. Refresh the library and select again.")
        if kind == "decks":
            slides = db.execute(f"SELECT id, deck_id FROM slides WHERE deck_id IN ({placeholders})", ids).fetchall()
        else:
            slides = db.execute(f"SELECT id, deck_id FROM slides WHERE id IN ({placeholders})", ids).fetchall()
        result = {"deck_count": len(ids) if kind == "decks" else 0, "slide_count": len(slides)}
        if not execute:
            return result
        if expected_slide_count != len(slides):
            raise HTTPException(409, "The slide count changed. Review the deletion again.")
        slide_ids = [row["id"] for row in slides]
        # These two tables use polymorphic IDs rather than foreign keys.
        for entity_type, entity_ids in [("slide", slide_ids), ("deck", ids if kind == "decks" else [])]:
            for entity_id in entity_ids:
                for related in ("edit_history", "ai_enrichment_results"):
                    db.execute(f"DELETE FROM {related} WHERE entity_type = ? AND entity_id = ?", (entity_type, entity_id))
        if kind == "decks":
            from app.ingestion import _finish_item
            pending=db.execute(f"SELECT id,job_id FROM import_job_items WHERE deck_id IN ({placeholders}) AND status IN ('queued','processing')",ids).fetchall()
            for item in pending:
                _finish_item(settings,item['job_id'],item['id'],'failed',message='Deck removed from the library during import; it will not be re-imported on recovery.',db=db)
            db.execute(f"UPDATE import_job_items SET deck_id = NULL WHERE deck_id IN ({placeholders})", ids)
        db.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        if kind == "slides":
            for deck_id in {row["deck_id"] for row in slides}:
                db.execute("UPDATE decks SET slide_count = (SELECT COUNT(*) FROM slides WHERE deck_id = ?) WHERE id = ?", (deck_id, deck_id))
        # Foreign-key cascades remove assignments, checksums, sources and render rows.
        # Managed files/cached images are retained; no filesystem deletion is needed.
        return result


def create_tag(settings, name, tag_type):
    name = " ".join(name.split())
    if not name:
        raise HTTPException(422, "Enter a tag name.")
    normalized = name.casefold()
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM tag_categories WHERE id=?", (tag_type,)).fetchone():
            raise HTTPException(422, "Unknown canonical tag category.")
        existing = db.execute("SELECT * FROM tags WHERE tag_type = ? AND normalized_name = ?", (tag_type, normalized)).fetchone()
        if not existing:
            existing = db.execute("SELECT t.* FROM tags t JOIN tag_aliases a ON a.tag_id=t.id WHERE t.tag_type=? AND a.normalized_alias=?", (tag_type, normalized)).fetchone()
        if existing:
            if existing["status"] != "active":
                raise HTTPException(409, "That tag is archived. Choose an active tag.")
            return dict(existing)
        tag_id = str(uuid.uuid4())
        db.execute("INSERT INTO tags (id, tag_type, name, normalized_name, created_at) VALUES (?, ?, ?, ?, ?)",
                   (tag_id, tag_type, name, normalized, datetime.now(timezone.utc).isoformat()))
        from app.taxonomy import audit
        audit(db, "create_tag", {"id": tag_id, "name": name, "tag_type": tag_type})
        return dict(db.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone())


def change_assignment(settings, kind, entity_id, tag_id, remove=False):
    entity_type = "deck" if kind == "decks" else "slide"
    with connect_database(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute(f"SELECT 1 FROM {kind} WHERE id = ?", (entity_id,)).fetchone():
            raise HTTPException(404, "Library item no longer exists.")
        ids = [tag["id"] for tag in assigned_tags(db, entity_type, entity_id)]
        if remove:
            ids = [value for value in ids if value != tag_id]
        elif tag_id not in ids:
            ids.append(tag_id)
        if len(ids) > 200:
            raise HTTPException(422, "An item can have at most 200 assigned tags.")
        try:
            replace_tags(db, entity_type, entity_id, ids)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"tags": assigned_tags(db, entity_type, entity_id)}
