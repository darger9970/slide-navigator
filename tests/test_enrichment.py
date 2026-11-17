import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app import enrichment as ai
from app.main import create_app
from test_library import library  # noqa: F401


def value(**updates):
    return {"title": "AI title", "description": "The presentation describes quarterly revenue performance and next steps based on the supplied content.",
            "organisation": None, "organisation_evidence": None, "tag_ids": [], "new_tags": [],
            "keywords": ["Revenue", "Planning"], "limited": False, "limitation": "", **updates}


@pytest.fixture
def ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "models", lambda settings: {"models": [{"name": "qwen3:4b"}, {"name": "qwen2.5-coder:14b"}], "running": [],
        "default_model": "qwen3:4b", "defaults": {"timeout_seconds": 180, "context_length": 8192, "concurrency": 1}})
    def generate(settings, model, options, schema, prompt):
        calls.append(prompt)
        if schema is ai.Evidence:
            return ai.Evidence(summary="Quarterly revenue increased by 12 percent. Next steps were discussed."), {"eval_count": 20, "eval_duration": 1000000000, "wall_seconds": 1}
        return ai.Suggestion.model_validate(value()), {"eval_count": 40, "eval_duration": 1000000000, "wall_seconds": 1}
    monkeypatch.setattr(ai, "generate", generate)
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: None)
    return calls


def start(client, deck_id, **updates):
    response = client.post("/api/enrichment/jobs", json={"deck_ids": [deck_id], "model": "qwen3:4b", **updates})
    assert response.status_code == 202, response.text
    return client.get(f"/api/enrichment/jobs/{response.json()['job_id']}").json()


def result(client, deck_id):
    return client.get(f"/api/enrichment/deck/{deck_id}/suggestions").json()["items"][0]


def test_generate_progress_review_skip_regenerate_and_persist(library, ollama):
    client, settings, decks, _ = library
    job = start(client, decks["untagged"])
    assert job["status"] == "completed"
    assert job["deck_progress"] == {"total": 1, "processed": 1}
    assert job["slide_progress"] == {"total": 2, "processed": 2}
    assert job["counts"]["completed"] == 3
    assert job["elapsed_seconds"] >= 0 and job["completed_at"]
    suggestion = result(client, decks["untagged"])
    assert suggestion["decision"] == "pending"
    assert suggestion["content"]["coverage_slide_count"] == 2
    assert client.get(f"/api/decks/{decks['untagged']}").json()["name"] == "Untagged"
    assert start(client, decks["untagged"])["counts"]["skipped"] == 3
    response = client.post("/api/enrichment/review", json={"result_ids": [suggestion["id"]], "action": "accept"})
    assert response.status_code == 200
    assert client.get(f"/api/decks/{decks['untagged']}").json()["name"] == "AI title"
    assert start(client, decks["untagged"], force=True)["counts"]["completed"] == 3
    regenerated = result(client, decks["untagged"])
    assert regenerated["id"] != suggestion["id"]
    assert client.post("/api/enrichment/review", json={"result_ids": [regenerated["id"]], "action": "reject"}).status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/decks/{decks['untagged']}").json()["name"] == "AI title"
        assert result(restarted, decks["untagged"])["decision"] == "rejected"
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM enrichment_reviews").fetchone()[0] == 2


def test_manual_and_approved_values_protected_during_acceptance(library, ollama):
    client, _, decks, slides = library
    deck_id = decks["alpha"]
    client.patch(f"/api/decks/{deck_id}", json={"title": "Manual title", "description": "Keep this", "review_status": "Reviewed"})
    client.patch(f"/api/slides/{slides['alpha'][0]['id']}", json={"title": "Manual slide", "description": "Keep slide"})
    job = start(client, deck_id)
    response = client.post("/api/enrichment/review", json={"result_ids": [i["result_id"] for i in job["items"]], "action": "accept"})
    assert response.status_code == 200
    assert {"title", "description"} <= set(response.json()["results"][0]["protected_fields"])
    assert client.get(f"/api/decks/{deck_id}").json()["name"] == "Manual title"
    assert client.get(f"/api/slides/{slides['alpha'][0]['id']}").json()["title"] == "Manual slide"


def test_stale_content_and_edits_after_generation_are_protected(library, ollama):
    client, settings, decks, _ = library
    deck_id = decks["alpha"]
    start(client, deck_id, include_slides=False)
    suggestion = result(client, deck_id)
    client.patch(f"/api/decks/{deck_id}", json={"title": "Changed during generation"})
    response = client.post("/api/enrichment/review", json={"result_ids": [suggestion["id"]], "action": "accept"})
    assert "title" in response.json()["results"][0]["protected_fields"]
    start(client, deck_id, include_slides=False, force=True)
    pending = result(client, deck_id)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE slides SET text_content = 'Changed evidence' WHERE deck_id = ?", (deck_id,))
    assert client.post("/api/enrichment/review", json={"result_ids": [pending["id"]], "action": "accept"}).status_code == 409


def test_tag_matching_explicit_creation_and_edited_suggestions(library, ollama, monkeypatch):
    client, settings, decks, _ = library
    def generate(settings, model, options, schema, prompt):
        return ai.Suggestion.model_validate(value(new_tags=[{"name": "Proposals", "tag_type": "deck_type"}, {"name": "New business theme", "tag_type": "topic"}])), {}
    monkeypatch.setattr(ai, "generate", generate)
    start(client, decks["untagged"], include_slides=False)
    suggestion = result(client, decks["untagged"])
    assert suggestion["content"]["suggestion"]["tag_ids"] == ["proposal"]
    assert len(suggestion["content"]["suggestion"]["new_tags"]) == 1
    tags_before = len(client.get("/api/filters").json()["tags"])
    edited = {**suggestion["content"]["suggestion"], "title": "Edited AI title"}
    response = client.post("/api/enrichment/review", json={"result_ids": [suggestion["id"]], "action": "accept", "edits": edited, "approve_new_tags": True})
    assert response.status_code == 200, response.text
    deck = client.get(f"/api/decks/{decks['untagged']}").json()
    assert deck["name"] == "Edited AI title"
    assert {t["name"] for t in deck["tags"]} == {"Proposal", "New business theme"}
    assert len(client.get("/api/filters").json()["tags"]) == tags_before + 1


def test_new_tags_not_created_without_approval(library, ollama, monkeypatch):
    client, _, decks, _ = library
    monkeypatch.setattr(ai, "generate", lambda *args: (ai.Suggestion.model_validate(value(new_tags=[{"name": "Needs approval", "tag_type": "topic"}])), {}))
    start(client, decks["alpha"], include_slides=False)
    suggestion = result(client, decks["alpha"])
    assert client.post("/api/enrichment/review", json={"result_ids": [suggestion["id"]], "action": "accept"}).status_code == 200
    assert "Needs approval" not in {t["name"] for t in client.get("/api/filters").json()["tags"]}


def test_failures_and_unsupported_models(library, ollama, monkeypatch):
    client, _, decks, _ = library
    assert client.post("/api/enrichment/jobs", json={"deck_ids": [decks["alpha"]], "model": "not-installed"}).status_code == 422
    def timeout(*args):
        raise httpx.ReadTimeout("Ollama timed out")
    monkeypatch.setattr(ai, "generate", timeout)
    job = start(client, decks["alpha"])
    assert job["status"] == "completed_with_errors"
    assert job["counts"]["failed"] == 2
    assert job["counts"]["completed"] == 1  # Text-limited slide needs no model call.
    assert any("timed out" in (i["error"] or "") for i in job["items"])
    monkeypatch.setattr(ai, "models", lambda settings: {"models": [{"name": "qwen3:4b"}], "running": [{"name": "other:14b"}]})
    assert start(client, decks["alpha"])["status"] == "failed"


def test_recovery_preserves_completed_items(library, ollama):
    client, settings, decks, _ = library
    job = start(client, decks["alpha"])
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE enrichment_jobs SET status = 'processing', completed_at = NULL WHERE id = ?", (job["id"],))
        db.execute("UPDATE enrichment_items SET status = 'processing' WHERE id = ?", (job["items"][0]["id"],))
    assert ai.recover_jobs(settings) == [job["id"]]
    ai.run_job(settings, job["id"])
    recovered = ai.get_job(settings, job["id"])
    assert recovered["status"] == "completed"
    assert recovered["counts"]["completed"] == 2 and recovered["counts"]["skipped"] == 1


def test_structured_ollama_transport_validates_schema(tmp_path, monkeypatch):
    from test_ingestion import make_settings
    original_client = httpx.Client
    def handle(request):
        body = json.loads(request.content)
        assert body["format"]["type"] == "object" and body["stream"] is False
        assert body["options"]["num_ctx"] == 8192
        return httpx.Response(200, json={"response": json.dumps(value()), "eval_count": 20, "eval_duration": 1000000000})
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    result, metrics = ai.generate(make_settings(tmp_path), "qwen3:4b", {"timeout_seconds": 30, "context_length": 8192}, ai.Suggestion, "Evidence")
    assert result.title == "AI title" and metrics["eval_count"] == 20
    with pytest.raises(ValueError):
        ai.Suggestion.model_validate({**value(), "unexpected": "bad"})
    with pytest.raises(ValueError):
        ai.canonicalize(ai.Suggestion.model_validate(value(tag_ids=["unknown"])), [], "deck")


def test_chunked_deck_covers_every_slide(library, ollama):
    client, settings, decks, _ = library
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE slides SET text_content = ? WHERE deck_id = ?", ("Revenue grew. " * 5000, decks["alpha"]))
    start(client, decks["alpha"], include_slides=False)
    suggestion = result(client, decks["alpha"])
    assert suggestion["content"]["chunks_analysed"] > 1
    assert suggestion["content"]["coverage_slide_count"] == 2
    assert any("content chunk" in prompt for prompt in ollama)


@pytest.mark.parametrize("response", ["not JSON", '{"title": "Missing required fields"}'])
def test_malformed_ollama_response_rejected(tmp_path, monkeypatch, response):
    from test_ingestion import make_settings
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"response": response}))
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(ValueError):
        ai.generate(make_settings(tmp_path), "qwen3:4b", {"timeout_seconds": 30, "context_length": 8192}, ai.Suggestion, "Evidence")
