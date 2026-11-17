import httpx
import pytest
from app import enrichment as ai
from test_library import library  # noqa: F401
from test_enrichment import ollama, value, start, result  # noqa: F401


def test_purpose_industry_keywords_approval_and_manual_protection(library, ollama, monkeypatch):
    client, _, decks, _ = library
    monkeypatch.setattr(ai, "generate", lambda *args: (ai.Suggestion.model_validate(value(purpose="Review quarterly performance", industry="Telecom", industry_evidence="Revenue increased", keywords=["Revenue", "Review"])), {}))
    start(client, decks["alpha"], include_slides=False)
    pending = result(client, decks["alpha"])
    assert client.post("/api/enrichment/review", json={"result_ids": [pending["id"]], "action": "accept"}).status_code == 200
    deck = client.get(f"/api/decks/{decks['alpha']}").json()
    assert deck["purpose"] == "Review quarterly performance" and deck["keywords"] == ["Revenue", "Review"]
    client.patch(f"/api/decks/{decks['alpha']}", json={"purpose": "User purpose", "keywords": ["Manual"]})
    start(client, decks["alpha"], include_slides=False, force=True)
    client.post("/api/enrichment/review", json={"result_ids": [result(client, decks["alpha"])["id"]], "action": "accept"})
    deck = client.get(f"/api/decks/{decks['alpha']}").json()
    assert deck["purpose"] == "User purpose" and deck["keywords"] == ["Manual"]


def test_transient_retry_is_bounded(tmp_path, monkeypatch):
    from test_ingestion import make_settings
    calls = []
    def transient(*args):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary failure")
        return ai.Evidence(summary="Evidence"), {}
    monkeypatch.setattr(ai, "_generate_once", transient)
    monkeypatch.setattr(ai.time, "sleep", lambda seconds: None)
    result, _ = ai.generate(make_settings(tmp_path), "qwen3:4b", {"retries": 1}, ai.Evidence, "content")
    assert result.summary == "Evidence" and len(calls) == 2
    calls.clear()
    with pytest.raises(httpx.ReadTimeout):
        ai.generate(make_settings(tmp_path), "qwen3:4b", {"retries": 0}, ai.Evidence, "content")
    assert len(calls) == 1


def test_accept_all_has_explicit_job_scope(library, ollama):
    client, _, decks, _ = library
    first = start(client, decks["alpha"])
    second = start(client, decks["beta"])
    response = client.post("/api/enrichment/review", json={"job_id": first["id"], "action": "accept"})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 3
    assert result(client, decks["beta"])["decision"] == "pending"
    assert client.post("/api/enrichment/review", json={"action": "accept"}).status_code == 422
    assert client.post("/api/enrichment/review", json={"job_id": second["id"], "result_ids": [first["items"][0]["result_id"]], "action": "accept"}).status_code == 422


def test_explicit_purpose_and_unsupported_client_are_grounded(library, ollama, monkeypatch):
    client, settings, decks, _ = library
    from app.database import connect_database
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE slides SET text_content = 'Northwind operates in telecom. The purpose of this presentation is to review quarterly performance. Revenue was stable.' WHERE deck_id = ?", (decks["alpha"],))
    monkeypatch.setattr(ai, "generate", lambda *args: (ai.Suggestion.model_validate(value(
        organisation="Northwind Telecom", organisation_evidence="Northwind operates in telecom.",
        description="Northwind Telecom reviewed quarterly performance.", keywords=["Northwind Telecom", "Review"])), {}))
    start(client, decks["alpha"], include_slides=False)
    suggestion = result(client, decks["alpha"])["content"]["suggestion"]
    assert suggestion["purpose"] == "to review quarterly performance"
    assert suggestion["organisation"] is None and suggestion["limited"]
    assert "Northwind Telecom" not in suggestion["description"]
    assert suggestion["keywords"] == ["Review"]
