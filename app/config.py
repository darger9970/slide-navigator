import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    imports_dir: Path
    thumbnails_dir: Path
    indexes_dir: Path
    ollama_base_url: str
    ollama_required_models: tuple[str, ...]


def load_settings() -> Settings:
    data_dir = Path(
        os.environ.get(
            "SLIDE_NAVIGATOR_DATA_DIR",
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SlideNavigator",
        )
    ).expanduser()
    required_models = tuple(
        model.strip()
        for model in os.environ.get(
            "OLLAMA_REQUIRED_MODELS", "qwen3:4b,nomic-embed-text"
        ).split(",")
        if model.strip()
    )
    return Settings(
        data_dir=data_dir,
        database_path=Path(
            os.environ.get("SLIDE_NAVIGATOR_DB_PATH", data_dir / "metadata.sqlite3")
        ).expanduser(),
        imports_dir=Path(
            os.environ.get("SLIDE_NAVIGATOR_IMPORTS_DIR", data_dir / "imports")
        ).expanduser(),
        thumbnails_dir=Path(
            os.environ.get("SLIDE_NAVIGATOR_THUMBNAILS_DIR", data_dir / "thumbnails")
        ).expanduser(),
        indexes_dir=Path(
            os.environ.get("SLIDE_NAVIGATOR_INDEXES_DIR", data_dir / "indexes")
        ).expanduser(),
        ollama_base_url=os.environ.get(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).rstrip("/"),
        ollama_required_models=required_models,
    )
