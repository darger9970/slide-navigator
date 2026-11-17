import io
import sqlite3
import zipfile
import base64
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

import app.ingestion as ingestion
from app.main import create_app
from test_ingestion import create_presentation, make_settings, post_files


def metadata_presentation(title="PowerPoint Presentation", created=datetime(2023, 1, 1)):
    deck = Presentation()
    props = deck.core_properties
    props.title = title
    props.author = "Original Author"
    props.subject = "Salesforce takeout"
    props.comments = "Original comments"
    props.keywords = "BT, Salesforce"
    props.last_modified_by = "Last Editor"
    props.created = created
    props.modified = datetime(2026, 11, 1)
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = "BT Salesforce"
    slide.placeholders[1].text = "A title-slide subtitle"
    output = io.BytesIO()
    deck.save(output)
    return output.getvalue()


def test_raw_metadata_year_title_review_and_duplicate_protection(tmp_path):
    settings = make_settings(tmp_path)
    original = metadata_presentation()
    name = "BT - Salesforce takeout - 27th May 2025.pptx"
    with TestClient(create_app(settings)) as client:
        submitted = post_files(client, [(name, original)])
        job = client.get(f"/api/import/jobs/{submitted['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        deck = client.get(f"/api/decks/{deck_id}").json()
        assert deck["name"] == name
        assert deck["year"] == 2025
        assert deck["year_source"] == "filename"
        assert deck["author"] == "Original Author"
        assert deck["subject"] == "Salesforce takeout"
        assert deck["description"] == "Original comments"
        assert deck["subtitle"] == "A title-slide subtitle"
        raw = deck["source_metadata"]
        assert raw["document_properties"]["title"] == "PowerPoint Presentation"
        assert raw["document_properties"]["created"].startswith("2023-")
        assert raw["document_properties"]["last_modified_by"] == "Last Editor"
        assert raw["document_properties"]["keywords"] == "BT, Salesforce"
        assert deck["metadata_provenance"]["subtitle"]["source"] == "title_slide_subtitle_placeholder"
        assert deck["review_status"] == "Not Reviewed"
        tag = client.post("/api/tags", json={"name": "Reviewed import", "tag_type": "deck_type"}).json()
        response = client.patch(f"/api/decks/{deck_id}", json={
            "title": "Manually reviewed title", "organisation": "BT", "year": 2027,
            "author": "Edited author", "subject": "Edited subject", "subtitle": "Edited subtitle",
            "description": "Edited description", "review_status": "Reviewed", "tag_ids": [tag["id"]],
        })
        assert response.status_code == 200
        saved = response.json()
        assert saved["reviewed_at"]
        assert saved["year_source"] == "manual"
        assert saved["source_metadata"] == raw
        duplicate = post_files(client, [("Other filename 2020.pptx", original)])
        assert client.get(f"/api/import/jobs/{duplicate['job_id']}").json()["duplicate_count"] == 1
        after_duplicate = client.get(f"/api/decks/{deck_id}").json()
        assert {key: value for key, value in after_duplicate.items() if key != "source_locations"} == {key: value for key, value in saved.items() if key != "source_locations"}
        saved = after_duplicate
        assert client.patch(f"/api/decks/{deck_id}", json={"year": 1800}).status_code == 422
        assert client.patch(f"/api/decks/{deck_id}", json={"source_metadata": {}}).status_code == 422
    with TestClient(create_app(settings)) as restarted:
        persisted = restarted.get(f"/api/decks/{deck_id}").json()
        assert persisted == saved
        assert restarted.get(f"/api/import/jobs/{submitted['job_id']}").json()["items"][0]["review_status"] == "Reviewed"


def test_intermediate_deck_slide_progress_and_archives(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    snapshots = []
    original_progress = ingestion._job_progress
    def capture(*args, **kwargs):
        original_progress(*args, **kwargs)
        snapshots.append(ingestion.get_import_job(settings, args[1]))
    monkeypatch.setattr(ingestion, "_job_progress", capture)
    archive = io.BytesIO()
    first = create_presentation("First")
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("nested/a.pptx", first)
        target.writestr("nested/b.pptx", create_presentation("Second"))
        target.writestr("nested/notes.txt", "Skipped, never extracted")
    with TestClient(create_app(settings)) as client:
        submitted = post_files(client, [("folder/batch.zip", archive.getvalue()), ("folder/duplicate.pptx", first), ("folder/broken.pptx", b"broken")])
        job = client.get(f"/api/import/jobs/{submitted['job_id']}").json()
        assert job["deck_progress"] == {"total": 4, "processed": 4, "successful": 2, "skipped": 1, "failed": 1, "total_known": True}
        assert job["skipped_count"] == 1
        assert job["progress_percent"] == 100
        assert job["status"] == "completed_with_errors"
        assert job["started_at"] and job["completed_at"] and job["elapsed_seconds"] >= 0
        assert job["current_deck"]["filename"] is None
        assert any(item["message"] for item in job["items"] if item["status"] == "failed")
        assert any(s["stage"] == "discovering_files" and s["progress_percent"] is None for s in snapshots)
        extracting = [s for s in snapshots if s["stage"] == "extracting_slides"]
        assert [s["current_deck"]["processed"] for s in extracting] == [0, 1, 2, 0, 1, 2]
        assert [s["current_deck"]["percent"] for s in extracting] == [0, 50, 100, 0, 50, 100]
        assert all(s["deck_progress"]["total"] == 4 for s in extracting)
        # Worker calls are idempotent; GET/refresh never starts processing.
        ingestion.process_import_job(settings, submitted["job_id"])
        again = client.get(f"/api/import/jobs/{submitted['job_id']}").json()
        assert again["elapsed_seconds"] == job["elapsed_seconds"]
        assert again["processed_items"] == job["processed_items"]
        assert client.get("/api/decks").json()["total"] == 2
        page = client.get(f"/api/import/jobs/{submitted['job_id']}?item_offset=1&item_limit=2").json()
        assert len(page["items"]) == 2 and page["items_total"] == 6


@pytest.mark.parametrize("filename,created,modified,expected,source", [
    ("Report 2025.pptx", "2023-01-01", "2026-01-01", 2025, "filename"),
    ("Report.pptx", "2023-01-01", "2026-01-01", 2023, "document_created"),
    ("Report.pptx", None, "2026-01-01", 2026, "filesystem_modified_fallback"),
    ("Report.pptx", None, None, None, "unknown"),
    ("Comparison 2023 2025.pptx", "2020-01-01", None, 2020, "document_created"),
])
def test_year_strategy_missing_properties(filename, created, modified, expected, source):
    from app.presentation_metadata import imported_metadata
    extracted = {"source_metadata": {"document_properties": {"created": created}, "slide_content": {}}, "slides": []}
    values, provenance = imported_metadata(extracted, filename, modified)
    assert values["year"] == expected
    assert provenance["year"]["source"] == source
    assert values["author"] is None and values["subtitle"] is None


def test_content_date_precedes_creation_and_metadata_missing(tmp_path):
    original = metadata_presentation("Specific document title")
    deck = Presentation(io.BytesIO(original))
    deck.slides[0].placeholders[1].text = "27th May 2025"
    output = io.BytesIO()
    deck.save(output)
    path = tmp_path / "report.pptx"
    path.write_bytes(output.getvalue())
    extracted = ingestion.extract_presentation(path)
    from app.presentation_metadata import imported_metadata
    values, provenance = imported_metadata(extracted, "report.pptx", None)
    assert values["name"] == "Specific document title"
    assert values["year"] == 2025 and provenance["year"]["source"] == "opening_slide_date"
    # Missing native properties must remain missing, not be fabricated.
    clean = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as source, zipfile.ZipFile(clean, "w") as target:
        for item in source.infolist():
            payload = source.read(item)
            if item.filename == "docProps/core.xml":
                payload = b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>'
            target.writestr(item, payload)
    path.write_bytes(clean.getvalue())
    extracted = ingestion.extract_presentation(path)
    assert extracted["source_metadata"]["document_properties"]["author"] == ""
    assert extracted["source_metadata"]["document_properties"]["created"] is None


def test_review_validation_and_defer_persist(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        job = post_files(client, [("review.pptx", metadata_presentation())])
        deck_id = client.get(f"/api/import/jobs/{job['job_id']}").json()["items"][0]["deck_id"]
        assert client.patch(f"/api/decks/{deck_id}", json={"review_status": "invalid", "title": "Must not save"}).status_code == 422
        assert client.patch(f"/api/decks/{deck_id}", json={"author": "a" * 251}).status_code == 422
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/decks/{deck_id}").json()["review_status"] == "Not Reviewed"


def test_legacy_ppt_metadata_uses_existing_conversion(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    def convert(settings, action, source, output):
        assert action == "convert"
        output.write_bytes(metadata_presentation("Legacy title"))
        return {"converted_path": str(output)}
    monkeypatch.setattr(ingestion, "_run_libreoffice_worker", convert)
    with TestClient(create_app(settings)) as client:
        job = post_files(client, [("legacy.ppt", b"mock legacy format")])
        item = client.get(f"/api/import/jobs/{job['job_id']}").json()["items"][0]
        deck = client.get(f"/api/decks/{item['deck_id']}").json()
        assert deck["author"] == "Original Author"
        assert deck["source_metadata"]["extraction"]["converted_with"] == "LibreOffice"
        assert deck["source_metadata"]["extraction"]["source_format"] == ".ppt"


def test_renderer_wait_is_indeterminate_and_cache_progress_is_real(tmp_path, monkeypatch):
    settings = replace(make_settings(tmp_path), rendering_enabled=True)
    snapshots = []
    original = ingestion._job_progress
    def capture(*args, **kwargs):
        original(*args, **kwargs)
        snapshots.append(ingestion.get_import_job(settings, args[1]))
    def renderer(settings, action, source, output):
        assert action == "render"
        output.mkdir(parents=True, exist_ok=True)
        paths = []
        for number in [1, 2]:
            path = output / f"slide-{number}.png"
            path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5V0AAAAASUVORK5CYII="))
            paths.append({"slide_number": number, "path": str(path)})
        return {"slides": paths}
    monkeypatch.setattr(ingestion, "libreoffice_available", lambda: True)
    monkeypatch.setattr(ingestion, "_run_libreoffice_worker", renderer)
    monkeypatch.setattr(ingestion, "_job_progress", capture)
    with TestClient(create_app(settings)) as client:
        job = post_files(client, [("renders.pptx", create_presentation())])
        result = client.get(f"/api/import/jobs/{job['job_id']}").json()
        assert result["items"][0]["rendering_results"] == [{"status": "available", "error": None, "slide_count": 2}]
    waiting = next(s for s in snapshots if s["stage"] == "generating_thumbnails")
    assert waiting["current_deck"]["percent"] is None
    assert [s["current_deck"]["processed"] for s in snapshots if s["stage"] == "saving_thumbnails"] == [0, 1, 2]


def test_fatal_job_failure_has_frozen_elapsed_and_preserves_completed_deck(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    original = ingestion._job_progress
    def fail_finalisation(*args, **kwargs):
        if args[2] == "finalising_import":
            raise RuntimeError("simulated job-level failure")
        original(*args, **kwargs)
    monkeypatch.setattr(ingestion, "_job_progress", fail_finalisation)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        submitted = post_files(client, [("retained.pptx", create_presentation())])
        job = client.get(f"/api/import/jobs/{submitted['job_id']}").json()
        assert job["status"] == "failed" and job["completed_at"]
        assert client.get("/api/decks").json()["total"] == 1
        assert client.get(f"/api/import/jobs/{submitted['job_id']}").json()["elapsed_seconds"] == job["elapsed_seconds"]
        assert client.get("/api/import/jobs?active_only=true").json()["total"] == 0


def test_large_job_status_paginates_without_premature_100_percent(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        now = ingestion._now()
        with sqlite3.connect(settings.database_path) as db:
            db.execute("""INSERT INTO import_jobs (id, status, storage_mode, created_at, started_at,
                total_items, processed_items, imported_count, discovery_complete, stage)
                VALUES ('large', 'processing', 'managed_copy', ?, ?, 5000, 4999, 4999, 1, 'extracting_metadata')""", (now, now))
            db.executemany("""INSERT INTO import_job_items (id, job_id, source_name, logical_source_path, status, created_at)
                VALUES (?, 'large', ?, ?, ?, ?)""", [(f"item-{i}", f"file-{i}.pptx", f"nested/file-{i}.pptx", "imported" if i < 4999 else "processing", now) for i in range(5000)])
        job = client.get("/api/import/jobs/large").json()
        assert job["deck_progress"]["total"] == 5000
        assert job["deck_progress"]["processed"] == 4999
        assert job["progress_percent"] == 99.9
        assert job["items_total"] == 5000 and len(job["items"]) == 100
        final_page = client.get("/api/import/jobs/large?item_offset=4900").json()
        assert len(final_page["items"]) == 100
        assert final_page["items"][-1]["id"] == "item-4999"
        assert client.get("/api/import/jobs/large?item_limit=501").status_code == 422
