"""Milestone 2B acceptance coverage using persisted canonical tags."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from test_ingestion import create_presentation, make_settings, post_files


@pytest.fixture
def library(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(client, [
            ("alpha.pptx", create_presentation("Alpha")),
            ("beta.pptx", create_presentation("Beta")),
            ("untagged.pptx", create_presentation("Untagged")),
        ])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        decks = {item["source_name"].split(".")[0]: item["deck_id"] for item in job["items"]}
        slides = {name: client.get("/api/slides", params={"deck_id": deck}).json()["items"] for name, deck in decks.items()}
        with sqlite3.connect(settings.database_path) as db:
            for tag_id, category, name, status in [
                ("proposal", "deck_type", "Proposal", "active"),
                ("strategy", "deck_type", "Strategy", "active"),
                ("agenda", "slide_type", "Agenda", "active"),
                ("team", "slide_type", "Team", "active"),
                ("cloud", "topic", "Cloud", "active"),
                ("unused", "topic", "Unused", "active"),
                ("archived", "topic", "Old topic", "archived"),
            ]:
                db.execute("INSERT INTO tags (id, tag_type, name, normalized_name, status, created_at) VALUES (?, ?, ?, ?, ?, '2026-01-01')", (tag_id, category, name, name.lower(), status))
            db.executemany("INSERT INTO deck_tags (deck_id, tag_id) VALUES (?, ?)", [
                (decks["alpha"], "proposal"), (decks["alpha"], "strategy"), (decks["beta"], "strategy"),
            ])
            db.executemany("INSERT INTO slide_tags (slide_id, tag_id) VALUES (?, ?)", [
                (slides["alpha"][0]["id"], "agenda"), (slides["alpha"][1]["id"], "agenda"),
                (slides["alpha"][0]["id"], "cloud"), (slides["beta"][0]["id"], "team"),
                (slides["beta"][0]["id"], "cloud"),
            ])
        yield client, settings, decks, slides


@pytest.mark.parametrize("path,params,total", [
    ("decks", [], 3),
    ("decks", [("deck_type", "proposal"), ("deck_type", "strategy")], 2),
    ("decks", [("deck_type", "strategy"), ("slide_type", "agenda")], 1),
    ("decks", [("slide_type", "agenda"), ("slide_type", "team")], 2),
    ("decks", [("deck_type", "proposal"), ("slide_type", "team")], 0),
    ("slides", [("slide_type", "agenda"), ("slide_type", "team")], 3),
    ("slides", [("slide_type", "agenda"), ("topic", "cloud")], 1),
    ("slides", [("topic", "cloud"), ("topic", "unused")], 2),
    ("slides", [("topic", "unknown")], 0),
    ("slides", [("slide_type", "cloud")], 0),
])
def test_tag_filter_logic(library, path, params, total):
    client, _, _, _ = library
    response = client.get(f"/api/{path}", params=params)
    assert response.status_code == 200
    assert response.json()["total"] == total
    assert len({item["id"] for item in response.json()["items"]}) == total


def test_tags_combine_with_metadata_search_sort_and_pagination(library):
    client, _, decks, _ = library
    deck = client.patch(f"/api/decks/{decks['alpha']}", json={"organisation": "Example", "year": 2026}).json()
    result = client.get("/api/slides", params={
        "deck_id": decks["alpha"], "organisation_id": deck["organisation_id"],
        "year": 2026, "slide_type": "agenda", "q": "Alpha", "limit": 1,
        "offset": 1, "sort_by": "slide_number", "sort_order": "desc",
    }).json()
    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["slide_number"] == 1


def test_canonical_tag_counts_and_empty_tags(library):
    client, _, decks, slides = library
    tags = {tag["id"]: tag for tag in client.get("/api/filters").json()["tags"]}
    assert "archived" not in tags
    assert tags["agenda"]["slide_count"] == 2
    assert tags["agenda"]["containing_deck_count"] == 1
    assert tags["strategy"]["deck_count"] == 2
    assert tags["unused"]["slide_count"] == 0
    assert client.get(f"/api/decks/{decks['untagged']}").json()["tags"] == []
    assert client.get(f"/api/slides/{slides['untagged'][0]['id']}").json()["tags"] == []
    deck = client.get(f"/api/decks/{decks['alpha']}").json()
    assert {tag["id"] for tag in deck["tags"]} == {"proposal", "strategy"}
    assert all("assignment_status" in tag for tag in deck["tags"])


def test_tag_edits_persist_with_history_and_update_counts(library):
    client, settings, decks, slides = library
    slide_id = slides["untagged"][0]["id"]
    assert client.patch(f"/api/slides/{slide_id}", json={"tag_ids": ["team", "cloud"]}).status_code == 200
    assert client.patch(f"/api/decks/{decks['untagged']}", json={"tag_ids": ["proposal"]}).status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert {t["id"] for t in restarted.get(f"/api/slides/{slide_id}").json()["tags"]} == {"team", "cloud"}
        tags = {t["id"]: t for t in restarted.get("/api/filters").json()["tags"]}
        assert tags["team"]["slide_count"] == 2
        assert tags["proposal"]["deck_count"] == 2
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM edit_history WHERE field_name = 'tag_ids'").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 7
    client.patch(f"/api/slides/{slide_id}", json={"tag_ids": []})
    assert client.get(f"/api/slides/{slide_id}").json()["tags"] == []


@pytest.mark.parametrize("kind,ids", [
    ("slides", ["proposal"]), ("decks", ["agenda"]),
    ("slides", ["missing"]), ("slides", ["team", "team"]),
    ("slides", ["archived"]), ("slides", [""]), ("slides", None),
])
def test_invalid_tag_edits_are_atomic(library, kind, ids):
    client, _, decks, slides = library
    entity_id = decks["alpha"] if kind == "decks" else slides["alpha"][0]["id"]
    url = f"/api/{kind}/{entity_id}"
    before = client.get(url).json()
    response = client.patch(url, json={"tag_ids": ids, "title": "Should not save"})
    assert response.status_code == 422
    assert client.get(url).json() == before


def test_archived_assignments_display_but_cannot_be_newly_assigned(library):
    client, settings, _, slides = library
    slide_id = slides["alpha"][0]["id"]
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO slide_tags (slide_id, tag_id) VALUES (?, 'archived')", (slide_id,))
    result = client.patch(f"/api/slides/{slide_id}", json={"tag_ids": ["agenda", "cloud", "archived"]})
    assert result.status_code == 200
    assert any(t["status"] == "archived" for t in result.json()["tags"])


@pytest.mark.parametrize("params", [[("topic", "")], [("topic", "a" * 101)], [("slide_type", "team")] * 101])
def test_invalid_filter_inputs(library, params):
    assert library[0].get("/api/slides", params=params).status_code == 422


def test_no_tags_available_is_an_empty_canonical_list(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/api/filters").json()["tags"] == []
        assert client.get("/api/decks?deck_type=missing").json()["items"] == []
        assert client.get("/api/slides?topic=missing").json()["items"] == []
