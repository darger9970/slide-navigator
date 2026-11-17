import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import app.ingestion as ingestion
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches

from app.config import Settings
from app.database import initialize_database
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "metadata.sqlite3",
        imports_dir=data_dir / "imports",
        thumbnails_dir=data_dir / "thumbnails",
        indexes_dir=data_dir / "indexes",
        staging_dir=data_dir / "staging",
        ollama_base_url="http://ollama.test",
        ollama_required_models=("qwen3:4b", "nomic-embed-text"),
        storage_mode="managed_copy",
        max_upload_file_bytes=5_000_000,
        max_archive_member_bytes=2_000_000,
        max_archive_total_bytes=5_000_000,
        max_archive_files=100,
        worker_timeout_seconds=10,
        rendering_enabled=False,
    )


def create_presentation(title: str = "Synthetic Deck") -> bytes:
    presentation = Presentation()
    presentation.core_properties.title = title

    first = presentation.slides.add_slide(presentation.slide_layouts[6])
    title_box = first.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    title_box.text = "Quarterly Review"
    body = first.shapes.add_textbox(Inches(1), Inches(2), Inches(5), Inches(1))
    body.text = "Revenue increased by 12 percent.\nOperating margin improved."
    table = first.shapes.add_table(
        2, 2, Inches(1), Inches(3), Inches(5), Inches(1)
    ).table
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "North"
    table.cell(1, 0).text = "Status"
    table.cell(1, 1).text = "On track"
    first.notes_slide.notes_text_frame.text = "Discuss the revenue trend."

    second = presentation.slides.add_slide(presentation.slide_layouts[6])
    second.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = (
        "Next steps"
    )
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def create_title_cases() -> bytes:
    presentation = Presentation()
    presentation.core_properties.title = "Title Cases"

    titled = presentation.slides.add_slide(presentation.slide_layouts[0])
    titled.shapes.title.text = "Executive Summary"

    inferred = presentation.slides.add_slide(presentation.slide_layouts[6])
    heading = inferred.shapes.add_textbox(
        Inches(1), Inches(0.5), Inches(6), Inches(0.8)
    )
    heading.text = "Recommended Approach"
    heading.text_frame.paragraphs[0].runs[0].font.size = Inches(0.25)

    untitled = presentation.slides.add_slide(presentation.slide_layouts[6])
    body = untitled.shapes.add_textbox(
        Inches(1), Inches(4), Inches(6), Inches(1)
    )
    body.text = "This is explanatory body copy."

    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def post_files(client: TestClient, files: list[tuple[str, bytes]]) -> dict[str, str]:
    request_files = [
        ("files", (name, content, "application/octet-stream"))
        for name, content in files
    ]
    metadata = [{"last_modified": 1_700_000_000_000} for _ in files]
    response = client.post(
        "/api/import",
        files=request_files,
        data={
            "file_metadata": json.dumps(metadata),
            "storage_mode": "managed_copy",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_pptx_import_extracts_text_notes_and_persists_original(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    original = create_presentation()
    with TestClient(create_app(settings)) as client:
        submitted = post_files(client, [("history/QBR.pptx", original)])
        job = client.get(f"/api/import/jobs/{submitted['job_id']}").json()
        assert job["status"] == "completed"
        assert job["imported_count"] == 1
        assert job["slide_count"] == 2

        deck_id = job["items"][0]["deck_id"]
        deck = client.get(f"/api/decks/{deck_id}").json()
        assert deck["name"] == "Synthetic Deck"
        assert deck["original_filename"] == "QBR.pptx"
        assert deck["source_locations"][0]["source_path"] is None
        assert deck["source_locations"][0]["logical_path"] == "history/QBR.pptx"
        assert "stored_path" not in deck

        slides = client.get(f"/api/slides?deck_id={deck_id}&limit=10").json()
        assert slides["total"] == 2
        first_slide = client.get(f"/api/slides/{slides['items'][0]['id']}").json()
        assert first_slide["slide_number"] == 1
        assert first_slide["internal_slide_id"]
        assert first_slide["title"] == "Quarterly Review"
        assert first_slide["title_source"] == "inferred"
        assert "Quarterly Review" in first_slide["text_content"]
        assert "North" in first_slide["text_content"]
        assert "On track" in first_slide["text_content"]
        assert (
            "Revenue increased by 12 percent.\nOperating margin improved.\n"
            "Region\nNorth\nStatus\nOn track"
        ) in first_slide["text_content"]
        assert first_slide["speaker_notes"] == "Discuss the revenue trend."
        thumbnail = client.get(
            f"/api/slides/{first_slide['id']}/thumbnail"
        )
        assert thumbnail.status_code == 200
        assert thumbnail.headers["content-type"].startswith("image/svg+xml")

    stored = settings.imports_dir / deck_id / "QBR.pptx"
    assert stored.read_bytes() == original
    assert stored.is_file()

    with TestClient(create_app(settings)) as restarted_client:
        assert restarted_client.get(f"/api/decks/{deck_id}").status_code == 200
        assert restarted_client.get("/api/slides?limit=1").json()["total"] == 2


def test_presentation_names_resolve_uuid_titles_to_original_filename(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    original = create_presentation("1c5e2a1934674550b67662439bcb0bbc")
    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("customer-review.pptx", original)])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]

        deck = client.get(f"/api/decks/{deck_id}").json()
        slides = client.get(f"/api/slides?deck_id={deck_id}").json()

    assert deck["id"] == deck_id
    assert deck["name"] == "customer-review.pptx"
    assert slides["items"][0]["deck_id"] == deck_id
    assert slides["items"][0]["deck_name"] == "customer-review.pptx"


def test_slide_title_sources_cover_placeholder_inference_and_fallback(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("title-cases.pptx", create_title_cases())])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        slides = client.get(f"/api/slides?deck_id={deck_id}&limit=10").json()

    assert [(slide["title"], slide["title_source"]) for slide in slides["items"]] == [
        ("Executive Summary", "placeholder"),
        ("Recommended Approach", "inferred"),
        ("Slide 3 — Untitled", "fallback"),
    ]


def test_multiple_uploads_and_exact_duplicate_detection(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    first = create_presentation("First Deck")
    second = create_presentation("Second Deck")
    with TestClient(create_app(settings)) as client:
        result = post_files(
            client,
            [("first.pptx", first), ("nested/second.pptx", second)],
        )
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        assert job["status"] == "completed"
        assert job["imported_count"] == 2
        assert job["slide_count"] == 4
        assert client.get("/api/decks?offset=0&limit=1").json()["total"] == 2

        duplicate = post_files(client, [("another-copy.pptx", first)])
        duplicate_job = client.get(
            f"/api/import/jobs/{duplicate['job_id']}"
        ).json()
        assert duplicate_job["status"] == "completed"
        assert duplicate_job["duplicate_count"] == 1
        assert duplicate_job["imported_count"] == 0
        assert client.get("/api/decks").json()["total"] == 2


def test_library_filters_sorting_and_pagination(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        imported = post_files(
            client,
            [
                ("alpha.pptx", create_presentation("Alpha deck")),
                ("beta.pptx", create_presentation("Beta deck")),
                ("gamma.pptx", create_presentation("Gamma deck")),
            ],
        )
        job = client.get(f"/api/import/jobs/{imported['job_id']}").json()
        deck_ids = {item["source_name"]: item["deck_id"] for item in job["items"]}

        updated = client.patch(
            f"/api/decks/{deck_ids['alpha.pptx']}",
            json={"organisation": "Northwind", "year": 2024},
        )
        assert updated.status_code == 200
        northwind_id = updated.json()["organisation_id"]
        client.patch(
            f"/api/decks/{deck_ids['beta.pptx']}",
            json={"organisation": "Northwind", "year": 2023},
        )
        client.patch(
            f"/api/decks/{deck_ids['gamma.pptx']}",
            json={"organisation": "Contoso", "year": 2024},
        )

        first_page = client.get(
            "/api/decks?sort_by=name&sort_order=asc&limit=1&offset=0"
        ).json()
        second_page = client.get(
            "/api/decks?sort_by=name&sort_order=asc&limit=1&offset=1"
        ).json()
        filtered_decks = client.get(
            f"/api/decks?organisation_id={northwind_id}&year=2024"
        ).json()
        filtered_slides = client.get(
            f"/api/slides?organisation_id={northwind_id}&year=2024"
        ).json()
        options = client.get("/api/filters").json()

    assert first_page["total"] == 3
    assert first_page["items"][0]["name"] == "Alpha deck"
    assert second_page["items"][0]["name"] == "Beta deck"
    assert [deck["name"] for deck in filtered_decks["items"]] == ["Alpha deck"]
    assert len(filtered_slides["items"]) == 2
    assert filtered_slides["items"][0]["organisation"] == "Northwind"
    assert filtered_slides["items"][0]["year"] == 2024
    assert {item["name"] for item in options["organisations"]} == {
        "Northwind",
        "Contoso",
    }
    assert options["years"] == [2024, 2023]


def test_metadata_edits_persist_and_protect_manual_descriptions(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(
            client, [("metadata.pptx", create_presentation("Original deck"))]
        )
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        slide_id = client.get(f"/api/slides?deck_id={deck_id}").json()["items"][0]["id"]

        deck_edit = client.patch(
            f"/api/decks/{deck_id}",
            json={
                "title": "Customer Proposal",
                "description": "Reviewed deck summary.",
                "organisation": " Fabrikam ",
                "year": 2025,
            },
        )
        slide_edit = client.patch(
            f"/api/slides/{slide_id}",
            json={"title": "Market outlook", "description": "Manual slide summary."},
        )
        assert deck_edit.status_code == 200
        assert slide_edit.status_code == 200
        assert deck_edit.json()["name"] == "Customer Proposal"
        assert deck_edit.json()["organisation"] == "Fabrikam"
        assert deck_edit.json()["year"] == 2025
        assert deck_edit.json()["description_status"] == "Manually Edited"
        assert slide_edit.json()["title"] == "Market outlook"
        assert slide_edit.json()["title_source"] == "manual"
        assert slide_edit.json()["description_status"] == "Manually Edited"

        title_only = client.patch(
            f"/api/slides/{slide_id}", json={"title": "Market outlook revised"}
        )
        assert title_only.status_code == 200
        assert title_only.json()["description"] == "Manual slide summary."
        assert title_only.json()["description_status"] == "Manually Edited"
        assert title_only.json()["text_content"]

    with TestClient(create_app(settings)) as restarted_client:
        deck = restarted_client.get(f"/api/decks/{deck_id}").json()
        slide = restarted_client.get(f"/api/slides/{slide_id}").json()
        with sqlite3.connect(settings.database_path) as connection:
            edits = connection.execute(
                """
                SELECT entity_type, field_name FROM edit_history
                WHERE entity_id IN (?, ?)
                """,
                (deck_id, slide_id),
            ).fetchall()
    assert deck["name"] == "Customer Proposal"
    assert deck["description"] == "Reviewed deck summary."
    assert deck["organisation"] == "Fabrikam"
    assert deck["year"] == 2025
    assert slide["title"] == "Market outlook revised"
    assert slide["description"] == "Manual slide summary."
    assert {tuple(row) for row in edits} >= {
        ("deck", "title"),
        ("deck", "description"),
        ("deck", "organisation"),
        ("deck", "year"),
        ("slide", "title"),
        ("slide", "description"),
    }


def test_metadata_patch_validates_fields_and_missing_records(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(
            client, [("validation.pptx", create_presentation("Validation deck"))]
        )
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        slide_id = client.get(f"/api/slides?deck_id={deck_id}").json()["items"][0]["id"]

        assert client.patch(f"/api/decks/{deck_id}", json={"unknown": "value"}).status_code == 422
        assert client.patch(f"/api/decks/{deck_id}", json={"title": " "}).status_code == 422
        assert client.patch(f"/api/decks/{deck_id}", json={"year": 1800}).status_code == 422
        assert client.patch(f"/api/slides/{slide_id}", json={"title": None}).status_code == 422
        assert client.patch("/api/decks/missing", json={"title": "No deck"}).status_code == 404
        assert client.patch("/api/slides/missing", json={"title": "No slide"}).status_code == 404


def test_library_migration_keeps_existing_deck_and_slide_records(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(
            client, [("preserved.pptx", create_presentation("Preserved"))]
        )
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        stored_path = settings.imports_dir / deck_id / "preserved.pptx"
        original_bytes = stored_path.read_bytes()

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE app_metadata SET value = '4' WHERE key = 'schema_version'"
        )
    initialize_database(settings.database_path)

    with sqlite3.connect(settings.database_path) as connection:
        deck = connection.execute(
            "SELECT name, slide_count FROM decks WHERE id = ?", (deck_id,)
        ).fetchone()
        slide_count = connection.execute(
            "SELECT COUNT(*) FROM slides WHERE deck_id = ?", (deck_id,)
        ).fetchone()[0]
    assert deck == ("Preserved", 2)
    assert slide_count == 2
    assert stored_path.read_bytes() == original_bytes


def test_zip_ingestion_preserves_nested_logical_source_path(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    presentation = create_presentation("Archive Deck")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/year/deck.pptx", presentation)

    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("batch.zip", archive_buffer.getvalue())])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        assert job["status"] == "completed"
        assert job["imported_count"] == 1
        assert job["slide_count"] == 2
        deck = client.get(f"/api/decks/{job['items'][1]['deck_id']}").json()
        assert (
            deck["source_locations"][0]["logical_path"]
            == "batch.zip!/nested/year/deck.pptx"
        )


def test_zip_traversal_is_rejected_without_creating_a_deck(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("../outside.pptx", create_presentation())

    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("unsafe.zip", archive_buffer.getvalue())])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        assert job["status"] == "completed_with_errors"
        assert job["failed_count"] == 1
        assert "unsafe" in job["items"][0]["message"].lower()
        assert client.get("/api/decks").json()["total"] == 0
    assert not (settings.data_dir.parent / "outside.pptx").exists()


def test_corrupt_and_unsupported_uploads_are_reported(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        result = post_files(
            client,
            [
                ("broken.pptx", b"not a presentation"),
                ("broken.zip", b"not a zip"),
                ("notes.txt", b"unsupported"),
            ],
        )
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        assert job["status"] == "completed_with_errors"
        assert job["failed_count"] == 3
        assert all(item["status"] == "failed" for item in job["items"])
        assert client.get("/api/decks").json()["total"] == 0


def test_reference_mode_rejected_for_browser_upload(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/import",
            files=[("files", ("deck.pptx", create_presentation(), "application/octet-stream"))],
            data={"storage_mode": "reference"},
        )
    assert response.status_code == 422
    assert "absolute file path" in response.json()["detail"]


def test_legacy_ppt_reports_missing_local_conversion_engine(
    monkeypatch, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(ingestion, "libreoffice_available", lambda: False)
    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("legacy.ppt", b"legacy input")])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
    assert job["failed_count"] == 1
    assert "LibreOffice is unavailable" in job["items"][0]["message"]


def test_thumbnail_fallback_is_recorded_when_no_renderer_is_available(
    monkeypatch, tmp_path: Path
) -> None:
    settings = replace(make_settings(tmp_path), rendering_enabled=True)
    monkeypatch.setattr(ingestion, "libreoffice_available", lambda: False)
    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("no-renderer.pptx", create_presentation())])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
        deck_id = job["items"][0]["deck_id"]
        slides = client.get(f"/api/slides?deck_id={deck_id}").json()
        assert job["status"] == "completed"
        assert all(slide["rendering_status"] == "unavailable" for slide in slides["items"])


def test_archive_limits_are_enforced(tmp_path: Path) -> None:
    settings = replace(
        make_settings(tmp_path),
        max_archive_member_bytes=100,
        max_archive_total_bytes=200,
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/data.txt", "x" * 1_000)

    with TestClient(create_app(settings)) as client:
        result = post_files(client, [("large.zip", archive_buffer.getvalue())])
        job = client.get(f"/api/import/jobs/{result['job_id']}").json()
    assert job["failed_count"] == 1
    assert "per-file extraction limit" in job["items"][0]["message"]


def test_existing_database_values_survive_schema_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
            [("schema_version", "1"), ("existing_setting", "preserve-me")],
        )

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'existing_setting'"
        ).fetchone() == ("preserve-me",)
        assert connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone() == ("12",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'slides'"
        ).fetchone() == ("slides",)


def test_existing_slide_titles_are_backfilled_without_overwriting_manual_edits(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('schema_version', '3')"
        )
        connection.execute(
            """
            CREATE TABLE slides (
                id TEXT PRIMARY KEY,
                slide_number INTEGER NOT NULL,
                title TEXT,
                text_content TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO slides (id, slide_number, title, text_content)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    "inferred",
                    1,
                    None,
                    "Market opportunity\nA complete body sentence.\nRegion | North",
                ),
                ("fallback", 2, None, "A complete explanatory sentence."),
                ("placeholder", 3, "Legacy title", "Body text."),
                ("manual", 4, "Legacy title", "Body text."),
            ],
        )

    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE slides SET title = 'User edited title', title_source = 'manual' "
            "WHERE id = 'manual'"
        )

    assert ingestion.backfill_slide_titles(database_path) == 3
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id, title, title_source FROM slides ORDER BY slide_number"
        ).fetchall()
        full_text = connection.execute(
            "SELECT text_content FROM slides WHERE id = 'inferred'"
        ).fetchone()[0]

    assert rows == [
        ("inferred", "Market opportunity", "inferred"),
        ("fallback", "Slide 2 — Untitled", "fallback"),
        ("placeholder", "Legacy title", "placeholder"),
        ("manual", "User edited title", "manual"),
    ]
    assert full_text == (
        "Market opportunity\nA complete body sentence.\nRegion\nNorth"
    )
