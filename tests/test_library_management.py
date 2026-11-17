"""Library deletion preserves source files and maintains canonical relationships."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from test_library import library  # noqa: F401


def delete(client, kind, ids, expected=None):
    preview = client.post(f"/api/library/{kind}/deletion-preview", json={"ids": ids})
    assert preview.status_code == 200
    counts = preview.json()
    return client.post(f"/api/library/{kind}/delete", json={
        "ids": ids, "expected_slide_count": counts["slide_count"] if expected is None else expected,
    })


def test_delete_decks_cascades_and_preserves_files(library):
    client, settings, decks, slides = library
    assert client.patch(f"/api/decks/{decks['alpha']}", json={"description": "Delete this metadata"}).status_code == 200
    assert client.patch(f"/api/slides/{slides['alpha'][0]['id']}", json={"description": "Delete this metadata"}).status_code == 200
    with sqlite3.connect(settings.database_path) as db:
        paths = [Path(row[0]) for row in db.execute("SELECT stored_path FROM decks")]
        original_bytes = {path: path.read_bytes() for path in paths}
        # A reference outside managed storage must also never be deleted.
        external = settings.data_dir.parent / "external-original.pptx"
        external.write_bytes(b"external source")
        db.execute("UPDATE decks SET stored_path = ?, storage_mode = 'reference' WHERE id = ?", (str(external), decks["beta"]))
    response = delete(client, "decks", [decks["alpha"], decks["beta"]])
    assert response.status_code == 200
    assert response.json() == {"deck_count": 2, "slide_count": 4}
    assert client.get("/api/decks").json()["total"] == 1
    assert client.get("/api/slides").json()["total"] == 2
    assert client.get(f"/api/decks/{decks['alpha']}").status_code == 404
    for slide in slides["alpha"] + slides["beta"]:
        assert client.get(f"/api/slides/{slide['id']}").status_code == 404
        assert client.get(f"/api/slides/{slide['id']}/thumbnail").status_code == 404
    counts = {tag["id"]: tag for tag in client.get("/api/filters").json()["tags"]}
    assert counts["strategy"]["deck_count"] == 0
    assert counts["agenda"]["slide_count"] == 0
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM file_checksums").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM source_locations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM slide_renderings").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM import_job_items WHERE deck_id IS NULL").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 7
        assert db.execute("SELECT COUNT(*) FROM edit_history").fetchone()[0] == 0
    assert external.read_bytes() == b"external source"
    assert all(path.read_bytes() == content for path, content in original_bytes.items())
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/slides").json()["total"] == 2


def test_slide_deletion_updates_counts_keeps_native_numbers_and_deck(library):
    client, settings, decks, slides = library
    response = delete(client, "slides", [slides["alpha"][0]["id"], slides["beta"][0]["id"]])
    assert response.status_code == 200
    deck = client.get(f"/api/decks/{decks['alpha']}").json()
    assert deck["slide_count"] == 1
    assert client.get("/api/slides", params={"deck_id": decks["alpha"]}).json()["items"][0]["slide_number"] == 2
    tags = {tag["id"]: tag for tag in client.get("/api/filters").json()["tags"]}
    assert tags["agenda"]["slide_count"] == 1
    assert tags["cloud"]["slide_count"] == 0
    remaining = [slide["id"] for slide in client.get("/api/slides").json()["items"]]
    assert delete(client, "slides", remaining).status_code == 200
    assert client.get("/api/slides").json()["total"] == 0
    assert client.get("/api/decks").json()["total"] == 3
    assert all(deck["slide_count"] == 0 for deck in client.get("/api/decks").json()["items"])
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_deletion_conflicts_and_invalid_inputs_are_atomic(library):
    client, _, decks, _ = library
    assert delete(client, "decks", [decks["alpha"]], expected=99).status_code == 409
    for ids, status in [([], 422), ([decks["alpha"]] * 2, 422), ([decks["alpha"], "missing"], 409)]:
        assert client.post("/api/library/decks/delete", json={"ids": ids, "expected_slide_count": 2}).status_code == status
    assert client.get("/api/decks").json()["total"] == 3
    assert client.get("/api/slides").json()["total"] == 6


def test_custom_tags_reuse_ids_and_assignment_changes_are_independent(library):
    client, settings, decks, slides = library
    tag = client.post("/api/tags", json={"name": "  Custom   Topic  ", "tag_type": "topic"}).json()
    same = client.post("/api/tags", json={"name": "custom topic", "tag_type": "topic"}).json()
    assert tag["id"] == same["id"]
    assert tag["name"] == "Custom Topic"
    existing = client.post("/api/tags", json={"name": "PROPOSAL", "tag_type": "deck_type"}).json()
    assert existing["id"] == "proposal"
    for kind, entity_id in [("decks", decks["alpha"]), ("slides", slides["alpha"][0]["id"])]:
        url = f"/api/{kind}/{entity_id}/tags/{tag['id']}"
        before = client.get(f"/api/{kind}/{entity_id}").json()["tags"]
        assert client.put(url).status_code == 200
        assert client.put(url).status_code == 200
        assert len(client.get(f"/api/{kind}/{entity_id}").json()["tags"]) == len(before) + 1
        assert client.delete(url).status_code == 200
        assert client.get(f"/api/{kind}/{entity_id}").json()["tags"] == before
    with TestClient(create_app(settings)) as restarted:
        assert tag["id"] in {item["id"] for item in restarted.get("/api/filters").json()["tags"]}
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("payload", [{"name": "  ", "tag_type": "topic"}, {"name": "x" * 101, "tag_type": "topic"}, {"name": "test", "tag_type": "invalid"}])
def test_tag_creation_validation(library, payload):
    assert library[0].post("/api/tags", json=payload).status_code == 422


def test_assignment_validation_and_archived_tag_reuse(library):
    client, _, decks, _ = library
    for tag_id in ["missing", "agenda", "archived"]:
        assert client.put(f"/api/decks/{decks['alpha']}/tags/{tag_id}").status_code == 422
    assert client.put("/api/decks/missing/tags/proposal").status_code == 404
    assert client.post("/api/tags", json={"name": "Old topic", "tag_type": "topic"}).status_code == 409
