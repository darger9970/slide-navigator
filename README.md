# Slide Navigator

Slide Navigator is a local-first Windows application for browsing and searching
historical PowerPoint slides and building new editable presentations. This first
milestone provides the application shell, local metadata database, import
interface, and local Ollama connectivity checks. Slide ingestion, indexing,
search, and deck generation are planned for later milestones.

For the authoritative frontend visual and interaction requirements, see
[docs/UI_DESIGN.md](docs/UI_DESIGN.md).

## Requirements

- Windows 11
- Python 3.13
- Ollama running locally for model status checks (optional)

No cloud API, API key, or external presentation upload is used.

## Install

From the repository folder in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configure local storage

By default, application data is stored outside the repository in
`%LOCALAPPDATA%\SlideNavigator`. Override individual paths with environment
variables or a `.env` file in the repository root:

```dotenv
SLIDE_NAVIGATOR_DATA_DIR=C:\Users\YourName\Documents\SlideNavigator
SLIDE_NAVIGATOR_DB_PATH=C:\Users\YourName\Documents\SlideNavigator\metadata.sqlite3
SLIDE_NAVIGATOR_IMPORTS_DIR=C:\Users\YourName\Documents\SlideNavigator\imports
SLIDE_NAVIGATOR_THUMBNAILS_DIR=C:\Users\YourName\Documents\SlideNavigator\thumbnails
SLIDE_NAVIGATOR_INDEXES_DIR=C:\Users\YourName\Documents\SlideNavigator\indexes
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_REQUIRED_MODELS=qwen3:4b,nomic-embed-text
```

The `.env` file, databases, imported presentations, thumbnails, and indexes are
excluded from Git. The import page currently allows selecting or dropping files
and folders for the next milestone; it does not yet copy, inspect, or upload
their contents.

## Start

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The health endpoint is
`/health`; Ollama status is available at `/api/ollama/status`.

The Settings page displays whether the local Ollama API responds and whether
each configured model is installed. It can be checked directly at
`http://127.0.0.1:8000/api/ollama/status`. A stopped or unreachable Ollama
service is reported as unavailable; it does not prevent the rest of the app
from running.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```
