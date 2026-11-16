from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, load_settings
from app.database import initialize_database

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    for path in (
        settings.data_dir,
        settings.imports_dir,
        settings.thumbnails_dir,
        settings.indexes_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    initialize_database(settings.database_path)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Slide Navigator", lifespan=lifespan)
    application.state.settings = settings or load_settings()
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    async def homepage() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/ollama/status")
    async def ollama_status() -> dict[str, object]:
        current_settings: Settings = application.state.settings
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{current_settings.ollama_base_url}/api/tags"
                )
                response.raise_for_status()
        except httpx.RequestError as error:
            return {
                "connected": False,
                "models": [],
                "required_models": list(current_settings.ollama_required_models),
                "error": str(error),
            }
        except httpx.HTTPStatusError as error:
            return {
                "connected": False,
                "models": [],
                "required_models": list(current_settings.ollama_required_models),
                "error": f"Ollama returned HTTP {error.response.status_code}",
            }

        payload = response.json()
        installed_names = {
            model.get("name")
            for model in payload.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
        models = [
            {
                "name": name,
                "available": (
                    name if ":" in name else f"{name}:latest"
                )
                in installed_names,
            }
            for name in current_settings.ollama_required_models
        ]
        return {
            "connected": True,
            "models": models,
            "required_models": list(current_settings.ollama_required_models),
            "error": None,
        }

    return application


app = create_app()
