"""Regenerate an existing deck and verify PNGs, original bytes and metadata."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import pymupdf
from app.config import load_settings
from app.database import connect_database
from app.ingestion import _sha256


def snapshot(settings, deck_id):
    with connect_database(settings.database_path) as db:
        deck = dict(db.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone())
        content = {"deck": deck, "slides": [dict(r) for r in db.execute("SELECT * FROM slides WHERE deck_id = ? ORDER BY id", (deck_id,))],
            "tags": [dict(r) for r in db.execute("SELECT * FROM deck_tags WHERE deck_id = ? ORDER BY tag_id", (deck_id,))]}
    return _sha256(Path(deck["stored_path"])), hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("deck_id"); args = parser.parse_args()
    settings = load_settings()
    before = snapshot(settings, args.deck_id)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
        response = client.post(f"/api/decks/{args.deck_id}/thumbnails"); response.raise_for_status()
        job_id = response.json()["job_id"]
        print("Thumbnail job started", job_id, flush=True)
        deadline = time.monotonic() + 600
        previous = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/thumbnail-jobs/{job_id}"); response.raise_for_status(); job = response.json()
            message = (job["status"], job["stage"], job["processed"], job["total"])
            if message != previous:
                print(message, flush=True); previous = message
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(1)
        if job["status"] != "completed":
            raise RuntimeError(job.get("error") or "Thumbnail generation did not complete")
        slides = []
        offset = 0
        while True:
            response = client.get("/api/slides", params={"deck_id": args.deck_id, "offset": offset, "limit": 100}); response.raise_for_status()
            page = response.json(); slides.extend(page["items"])
            if len(slides) >= page["total"]: break
            offset += 100
        for slide in slides:
            response = client.get(f"/api/slides/{slide['id']}/thumbnail"); response.raise_for_status()
            assert response.headers["content-type"].startswith("image/png")
            image = pymupdf.Pixmap(response.content)
            assert image.width == 640 and image.height > 0
        after = snapshot(settings, args.deck_id)
        assert before == after, "Source bytes or library metadata changed"
        print(json.dumps({"png_previews_verified": len(slides), "source_unchanged": before[0] == after[0], "metadata_unchanged": before[1] == after[1]}), flush=True)


if __name__ == "__main__": main()
