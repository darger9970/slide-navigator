from dataclasses import replace
import io

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from app import thumbnails
from app.database import connect_database
from app.ingestion import libreoffice_available
from app.main import create_app
from app.rendering import libreoffice_executable
from test_ingestion import make_settings, post_files, create_presentation


def test_renderer_discovery_uses_explicit_path(tmp_path, monkeypatch):
    executable = tmp_path / "soffice"
    executable.write_text("configured renderer")
    monkeypatch.setenv("SLIDE_NAVIGATOR_LIBREOFFICE_PATH", str(executable))
    assert libreoffice_executable() == str(executable)


def test_worker_stdout_is_json_even_when_renderer_logs(monkeypatch, capsys, tmp_path):
    from app import libreoffice_worker
    import json
    import sys
    def noisy(*args):
        print("library diagnostic")
        return {"slides": []}
    monkeypatch.setattr(libreoffice_worker, "run_worker", noisy)
    monkeypatch.setattr(sys, "argv", ["worker", "--action", "render", "--source", str(tmp_path / "source.pptx"), "--output", str(tmp_path / "out")])
    assert libreoffice_worker.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"slides": []}
    assert "library diagnostic" in output.err


def test_retry_validation_and_single_active_job(tmp_path, monkeypatch):
    settings = replace(make_settings(tmp_path), rendering_enabled=True)
    monkeypatch.setattr("app.ingestion.libreoffice_available", lambda: False)
    with TestClient(create_app(settings)) as client:
        job = post_files(client, [("deck.pptx", create_presentation())])
        deck_id = client.get(f"/api/import/jobs/{job['job_id']}").json()["items"][0]["deck_id"]
        monkeypatch.setattr(thumbnails, "libreoffice_available", lambda: False)
        assert client.post(f"/api/decks/{deck_id}/thumbnails").status_code == 503
        monkeypatch.setattr(thumbnails, "libreoffice_available", lambda: True)
        first = thumbnails.create_job(settings, deck_id)
        assert thumbnails.create_job(settings, deck_id)["job_id"] == first["job_id"]
        assert client.post("/api/decks/missing/thumbnails").status_code == 404
        assert client.get("/api/thumbnail-jobs/missing").status_code == 404


@pytest.mark.skipif(not libreoffice_available(), reason="Real LibreOffice is not installed")
def test_real_render_retry_after_slide_deletion_preserves_source_and_metadata(tmp_path):
    settings = replace(make_settings(tmp_path), worker_timeout_seconds=180)
    original = create_presentation("Real rendering")
    app = create_app(settings)
    with TestClient(app) as client:
        imported = post_files(client, [("render.pptx", original)])
        deck_id = client.get(f"/api/import/jobs/{imported['job_id']}").json()["items"][0]["deck_id"]
        slides = client.get("/api/slides", params={"deck_id": deck_id}).json()["items"]
        assert client.get(f"/api/slides/{slides[0]['id']}/thumbnail").headers["content-type"].startswith("image/svg+xml")
        client.post("/api/library/slides/delete", json={"ids": [slides[0]["id"]], "expected_slide_count": 1})
        client.patch(f"/api/decks/{deck_id}", json={"title": "Keep edited title", "description": "Keep edited description", "year": 2025, "review_status": "Reviewed"})
        before = client.get(f"/api/decks/{deck_id}").json()
        app.state.settings = replace(settings, rendering_enabled=True)
        response = client.post(f"/api/decks/{deck_id}/thumbnails")
        assert response.status_code == 202, response.text
        status = client.get(f"/api/thumbnail-jobs/{response.json()['job_id']}").json()
        assert status["status"] == "completed", status
        assert status["processed"] == 1
        preview = client.get(f"/api/slides/{slides[1]['id']}/thumbnail")
        assert preview.headers["content-type"].startswith("image/png")
        assert preview.headers["cache-control"] == "no-cache"
        pixels = fitz.Pixmap(preview.content)
        assert pixels.width == 640 and pixels.height > 0
        after = client.get(f"/api/decks/{deck_id}").json()
        changing = {"thumbnail_status", "cover_thumbnail_version"}
        assert {k: v for k, v in before.items() if k not in changing} == {k: v for k, v in after.items() if k not in changing}
        assert (settings.imports_dir / deck_id / "render.pptx").read_bytes() == original
        assert after["thumbnail_status"]["available"] == 1
        assert client.get("/api/slides").json()["total"] == 1
    with TestClient(create_app(replace(settings, rendering_enabled=True))) as restarted:
        assert restarted.get(f"/api/slides/{slides[1]['id']}/thumbnail").headers["content-type"].startswith("image/png")
        fresh = post_files(restarted, [("fresh.pptx", create_presentation("Fresh import"))])
        fresh_item = restarted.get(f"/api/import/jobs/{fresh['job_id']}").json()["items"][0]
        assert restarted.get(f"/api/decks/{fresh_item['deck_id']}").json()["thumbnail_status"]["available"] == 2


@pytest.mark.skipif(not libreoffice_available(), reason="Real LibreOffice is not installed")
def test_real_legacy_ppt_conversion_and_previews(tmp_path):
    from app.libreoffice_worker import _convert
    source = tmp_path / "legacy-source.pptx"
    source.write_bytes(create_presentation("Legacy conversion"))
    legacy = _convert(source, tmp_path / "legacy", "ppt")
    original = legacy.read_bytes()
    settings = replace(make_settings(tmp_path), rendering_enabled=True, worker_timeout_seconds=180)
    with TestClient(create_app(settings)) as client:
        uploaded = post_files(client, [("legacy.ppt", original)])
        item = client.get(f"/api/import/jobs/{uploaded['job_id']}").json()["items"][0]
        assert item["status"] == "imported", item
        deck = client.get(f"/api/decks/{item['deck_id']}").json()
        assert deck["thumbnail_status"]["available"] == 2
        assert (settings.imports_dir / item["deck_id"] / "legacy.ppt").read_bytes() == original


@pytest.mark.skipif(not libreoffice_available(), reason="Real LibreOffice is not installed")
def test_hidden_slides_render_in_native_order_on_import(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    from app.libreoffice_worker import _convert
    deck = Presentation(io.BytesIO(create_presentation("Hidden slides")))
    deck.slides[0]._element.set("show", "0")
    last = deck.slides.add_slide(deck.slide_layouts[6])
    last._element.set("show", "0")
    last.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "Hidden final content"
    source = tmp_path / "hidden.pptx"; deck.save(source)
    original = source.read_bytes()
    pdf = _convert(source, tmp_path / "pdf", "pdf")
    with fitz.open(pdf) as pages:
        assert len(pages) == 3
        assert "Quarterly Review" in pages[0].get_text()
        assert "Next steps" in pages[1].get_text()
        assert "Hidden final content" in pages[2].get_text()
    settings = replace(make_settings(tmp_path), rendering_enabled=True, worker_timeout_seconds=180)
    with TestClient(create_app(settings)) as client:
        imported = post_files(client, [("hidden.pptx", original)])
        item = client.get(f"/api/import/jobs/{imported['job_id']}").json()["items"][0]
        saved = client.get(f"/api/decks/{item['deck_id']}").json()
        assert saved["thumbnail_status"]["available"] == 3
        assert (settings.imports_dir / item["deck_id"] / "hidden.pptx").read_bytes() == original
    assert source.read_bytes() == original
