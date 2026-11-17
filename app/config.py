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
    staging_dir: Path
    ollama_base_url: str
    ollama_required_models: tuple[str, ...]
    storage_mode: str
    max_upload_file_bytes: int
    max_archive_member_bytes: int
    max_archive_total_bytes: int
    max_archive_files: int
    worker_timeout_seconds: int
    rendering_enabled: bool
    enrichment_model: str = "qwen2.5-coder:14b"
    enrichment_timeout_seconds: int = 180
    enrichment_context_length: int = 8192
    enrichment_concurrency: int = 1
    enrichment_retries: int = 1
    embedding_model: str = "nomic-embed-text"
    embedding_timeout_seconds: int = 120


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
    storage_mode = os.environ.get(
        "SLIDE_NAVIGATOR_STORAGE_MODE", "managed_copy"
    ).strip().lower()
    if storage_mode not in {"managed_copy", "reference"}:
        raise ValueError(
            "SLIDE_NAVIGATOR_STORAGE_MODE must be 'managed_copy' or 'reference'"
        )
    limits = {
        "max_upload_file_bytes": int(
            os.environ.get("SLIDE_NAVIGATOR_MAX_UPLOAD_FILE_BYTES", 1_073_741_824)
        ),
        "max_archive_member_bytes": int(
            os.environ.get("SLIDE_NAVIGATOR_MAX_ARCHIVE_MEMBER_BYTES", 536_870_912)
        ),
        "max_archive_total_bytes": int(
            os.environ.get("SLIDE_NAVIGATOR_MAX_ARCHIVE_TOTAL_BYTES", 2_147_483_648)
        ),
        "max_archive_files": int(
            os.environ.get("SLIDE_NAVIGATOR_MAX_ARCHIVE_FILES", 5_000)
        ),
        "worker_timeout_seconds": int(
            os.environ.get("SLIDE_NAVIGATOR_WORKER_TIMEOUT_SECONDS", 180)
        ),
    }
    if any(value <= 0 for value in limits.values()):
        raise ValueError("Upload, archive, and worker limits must be positive")
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
        staging_dir=data_dir / "staging",
        ollama_base_url=os.environ.get(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).rstrip("/"),
        ollama_required_models=required_models,
        storage_mode=storage_mode,
        **limits,
        rendering_enabled=os.environ.get(
            "SLIDE_NAVIGATOR_RENDERING_ENABLED", "true"
        ).strip().lower() in {"1", "true", "yes", "on"},
        enrichment_model=os.environ.get("OLLAMA_ENRICHMENT_MODEL", "qwen2.5-coder:14b"),
        enrichment_timeout_seconds=int(os.environ.get("OLLAMA_ENRICHMENT_TIMEOUT_SECONDS", 180)),
        enrichment_context_length=int(os.environ.get("OLLAMA_ENRICHMENT_CONTEXT_LENGTH", 8192)),
        enrichment_concurrency=int(os.environ.get("OLLAMA_ENRICHMENT_CONCURRENCY", 1)),
        enrichment_retries=int(os.environ.get("OLLAMA_ENRICHMENT_RETRIES", 1)),
        embedding_model=os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
        embedding_timeout_seconds=int(os.environ.get("OLLAMA_EMBEDDING_TIMEOUT_SECONDS", 120)),
    )
