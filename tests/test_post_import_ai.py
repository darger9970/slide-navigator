import json
import time

from fastapi.testclient import TestClient

from app import enrichment
from app.main import create_app
from app.database import connect_database
from test_ingestion import make_settings, create_presentation
from test_enrichment import ollama  # noqa: F401


def upload(client, *, enabled=True, content=None):
    data = {"enrichment_options": json.dumps({"model": "qwen3:4b"})} if enabled else {}
    response = client.post("/api/import", files=[("files", ("new.pptx", content or create_presentation("Opt in")))], data=data)
    assert response.status_code == 202, response.text
    return client.get(f"/api/import/jobs/{response.json()['job_id']}").json()


def finished(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/enrichment/jobs/{job_id}").json()
        if job["status"] not in {"queued", "processing"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Enrichment did not finish")


def test_opt_in_import_schedules_once_and_never_accepts_metadata(tmp_path, ollama):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        imported = upload(client)
        assert imported["status"] == "completed" and imported["enrichment_job_id"]
        assert finished(client, imported["enrichment_job_id"])["counts"]["completed"] == 3
        deck_id = imported["items"][0]["deck_id"]
        deck = client.get(f"/api/decks/{deck_id}").json()
        assert deck["name"] == "Opt in" and deck["tags"] == []
        assert deck["metadata_states"]["title"] == "existing_unreviewed"
        enrichment.finish_post_import(settings, imported["id"])
        assert len(client.get("/api/enrichment/jobs").json()["items"]) == 1
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/import/jobs/{imported['id']}").json()["enrichment_job_id"] == imported["enrichment_job_id"]
        assert len(restarted.get("/api/enrichment/jobs").json()["items"]) == 1


def test_import_without_opt_in_and_duplicate_add_no_jobs(tmp_path, ollama):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        source = create_presentation("Existing")
        initial = upload(client, enabled=False, content=source)
        assert initial["enrichment_options"] is None
        assert client.get("/api/enrichment/jobs").json()["items"] == []
        duplicate = upload(client, content=source)
        assert duplicate["duplicate_count"] == 1
        assert "no new decks" in duplicate["enrichment_error"]
        assert client.get("/api/enrichment/jobs").json()["items"] == []


def test_unavailable_ai_preserves_successful_import(tmp_path, ollama, monkeypatch):
    from fastapi import HTTPException
    def unavailable(settings):
        raise HTTPException(503, "Ollama unavailable")
    monkeypatch.setattr(enrichment, "models", unavailable)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        job = upload(client)
        assert job["status"] == "completed" and job["imported_count"] == 1
        assert "unavailable" in job["enrichment_error"]
        assert client.get("/api/decks").json()["total"] == 1
        response = client.post("/api/import", files=[("files", ("test.pptx", create_presentation()))],
                               data={"enrichment_options": '{"model":"qwen3:4b","all_decks":true}'})
        assert response.status_code == 422


def test_slide_states_separate_approved_from_user_override(tmp_path, ollama):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        imported = upload(client)
        job = finished(client, imported["enrichment_job_id"])
        slide_item = next(item for item in job["items"] if item["entity_type"] == "slide")
        assert client.post("/api/enrichment/review", json={"result_ids": [slide_item["result_id"]], "action": "accept"}).status_code == 200
        slide = client.get(f"/api/slides/{slide_item['entity_id']}").json()
        assert slide["metadata_states"]["title"] == "approved"
        assert slide["metadata_states"]["description"] == "approved"
        changed = client.patch(f"/api/slides/{slide_item['entity_id']}", json={"title": "User override"}).json()
        assert changed["metadata_states"]["title"] == "user_edited"
        assert changed["metadata_states"]["description"] == "approved"


def test_restart_relinks_interrupted_hand_off_without_regeneration(tmp_path, ollama):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        imported = upload(client)
        original_id = imported["enrichment_job_id"]
        finished(client, original_id)
        calls = len(ollama)
        with connect_database(settings.database_path) as db:
            db.execute("UPDATE import_jobs SET enrichment_job_id = NULL WHERE id = ?", (imported["id"],))
    with TestClient(create_app(settings)) as restarted:
        for _ in range(100):
            restored = restarted.get(f"/api/import/jobs/{imported['id']}").json()
            if restored["enrichment_job_id"]:
                break
            time.sleep(0.02)
        assert restored["enrichment_job_id"] == original_id
        assert len(restarted.get("/api/enrichment/jobs").json()["items"]) == 1
        assert len(ollama) == calls
