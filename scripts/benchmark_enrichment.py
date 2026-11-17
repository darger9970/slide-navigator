"""Explicit real-Ollama integration benchmark; never edits the user's library."""

import io
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches

from app import enrichment
from app.config import load_settings
from app.main import create_app


def main():
    report = {"fixture": "Synthetic two-slide quarterly review; no private presentation content", "models": []}
    selected_models = sys.argv[1:] or enrichment.MODEL_NAMES
    if any(model not in enrichment.MODEL_NAMES for model in selected_models):
        raise SystemExit("Use supported model names only.")
    if sys.argv[1:] and Path("docs/OLLAMA_BENCHMARK.json").exists():
        report = json.loads(Path("docs/OLLAMA_BENCHMARK.json").read_text(encoding="utf-8"))
        report["models"] = [entry for entry in report["models"] if entry["model"] not in selected_models]
    settings = load_settings()
    original_generate = enrichment.generate
    resources = []
    def measured(*args, **kwargs):
        value = original_generate(*args, **kwargs)
        try:
            resources.append(httpx.get(settings.ollama_base_url + "/api/ps", timeout=10).json())
        except httpx.HTTPError:
            pass
        return value
    enrichment.generate = measured
    for model in selected_models:
        print(f"Starting real model: {model}", flush=True)
        with tempfile.TemporaryDirectory(prefix="slide-navigator-ai-benchmark-") as directory:
            root = Path(directory)
            isolated = replace(settings, data_dir=root, database_path=root / "metadata.sqlite3",
                imports_dir=root / "imports", thumbnails_dir=root / "thumbnails", indexes_dir=root / "indexes",
                staging_dir=root / "staging", rendering_enabled=False, enrichment_timeout_seconds=180)
            presentation = Presentation()
            presentation.core_properties.title = "Northwind quarterly business review"
            for text in [
                "Northwind quarterly business review. Revenue increased by 12 percent compared with the previous quarter. Operating margin improved. The team discussed the revenue trend and regional performance.",
                "Northwind next steps. The team will review regional performance monthly, document customer feedback, and track progress against the existing operating plan. No new budget or delivery dates were agreed.",
            ]:
                slide = presentation.slides.add_slide(presentation.slide_layouts[6])
                slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(3)).text = text
            data = io.BytesIO(); presentation.save(data)
            with TestClient(create_app(isolated)) as client:
                uploaded = client.post("/api/import", files=[("files", ("Northwind 2025.pptx", data.getvalue()))]).json()
                deck_id = client.get(f"/api/import/jobs/{uploaded['job_id']}").json()["items"][0]["deck_id"]
                resources.clear()
                started = time.perf_counter()
                response = client.post("/api/enrichment/jobs", json={"deck_ids": [deck_id], "model": model, "context_length": 8192, "concurrency": 1})
                response.raise_for_status()
                job = client.get(f"/api/enrichment/jobs/{response.json()['job_id']}").json()
                wall = time.perf_counter() - started
                results = []
                for item in job["items"]:
                    if item["result_id"]:
                        rows = client.get(f"/api/enrichment/{item['entity_type']}/{item['entity_id']}/suggestions").json()["items"]
                        result = next(row for row in rows if row["id"] == item["result_id"])
                        results.append({"entity_type": item["entity_type"], "suggestion": result["content"]["suggestion"], "metrics": result["content"]["metrics"]})
                tokens = sum(m.get("eval_count", 0) for row in results for m in row["metrics"])
                duration = sum(m.get("eval_duration", 0) for row in results for m in row["metrics"]) / 1e9
                entry = {"model": model, "status": job["status"], "wall_seconds": wall, "counts": job["counts"],
                         "generation_tokens_per_second": tokens / duration if duration else None,
                         "items": job["items"], "results": results, "resource_snapshots": list(resources)}
                report["models"].append(entry)
                print(json.dumps({k: entry[k] for k in ["model", "status", "wall_seconds", "counts", "generation_tokens_per_second"]}), flush=True)
                Path("docs/OLLAMA_BENCHMARK.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
