import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import initialize_database
from app.main import create_app


def make_settings(tmp_path: Path, ollama_url: str = "http://ollama.test") -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "metadata.sqlite3",
        imports_dir=data_dir / "imports",
        thumbnails_dir=data_dir / "thumbnails",
        indexes_dir=data_dir / "indexes",
        ollama_base_url=ollama_url,
        ollama_required_models=("qwen3:4b", "nomic-embed-text"),
    )


def test_health_endpoint_initializes_local_storage(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}

    assert settings.database_path.exists()
    assert settings.imports_dir.is_dir()
    assert settings.thumbnails_dir.is_dir()
    assert settings.indexes_dir.is_dir()


def test_database_initialization_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "metadata.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'schema_version'"
        ).fetchone()
    assert result == ("1",)


def test_ollama_status_reports_required_models(monkeypatch, tmp_path: Path) -> None:
    async def mock_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:4b"}, {"name": "extra:latest"}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/api/ollama/status")

    assert response.status_code == 200
    assert response.json() == {
        "connected": True,
        "models": [
            {"name": "qwen3:4b", "available": True},
            {"name": "nomic-embed-text", "available": False},
        ],
        "required_models": ["qwen3:4b", "nomic-embed-text"],
        "error": None,
    }


def test_ollama_status_reports_service_unavailable(monkeypatch, tmp_path: Path) -> None:
    async def mock_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("Ollama is not running", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/api/ollama/status")

    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["models"] == []
    assert "Ollama is not running" in response.json()["error"]


def test_ollama_status_matches_untagged_model_to_latest(
    monkeypatch, tmp_path: Path
) -> None:
    async def mock_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"models": [{"name": "nomic-embed-text:latest"}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/api/ollama/status")

    model = next(
        model for model in response.json()["models"] if model["name"] == "nomic-embed-text"
    )
    assert model["available"] is True
