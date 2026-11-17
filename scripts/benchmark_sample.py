"""Real-model comparison across business, technical and text-limited fixtures."""
import io
import json
import sqlite3
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
from app import enrichment
from app.config import load_settings
from app.main import create_app

SAMPLES = {
    "business": [
        "Northwind quarterly review. Revenue increased by 12 percent against the previous quarter. Operating margin improved, but no margin percentage was recorded. Customer retention remained stable. Regional performance varied and the team requested further analysis before attributing causes. This review describes historical performance; it is not a sales forecast or a claim of guaranteed future growth.",
        "Northwind next steps. The team will review regional performance monthly and document customer feedback. Operations owns the reporting process. Finance will validate the next reporting pack. No new budget, delivery date or staffing commitment was approved. A proposed workshop remains subject to client agreement. The presentation does not establish a target revenue increase for next quarter.",
    ],
    "technical": [
        "Contoso platform architecture. The current prototype uses FastAPI, SQLite and local file storage. Browser clients request metadata over HTTP. Presentation files remain editable and are not modified by indexing. The prototype has been exercised on Windows. Linux container compatibility is a design goal, but a production Azure deployment has not been tested. No cloud AI service is required.",
        "Contoso architecture review. Current keyword search operates on extracted text. Semantic search is outside the prototype scope. The team proposes persistent storage, deployment checks and workload measurement before production rollout. The presentation contains no measured availability percentage, throughput guarantee or approved migration deadline. Retaining human approval for generated metadata is a requirement, not evidence that automated classification is always correct.",
    ],
    "sparse": ["Roadmap"],
}


def main():
    report = {"sample_kind": "Synthetic representative scenarios; not a production-corpus quality study", "references": SAMPLES, "models": []}
    selected = sys.argv[1:] or enrichment.MODEL_NAMES
    if any(model not in enrichment.MODEL_NAMES for model in selected):
        raise SystemExit("Choose supported model names.")
    if sys.argv[1:] and Path("docs/OLLAMA_SAMPLE_BENCHMARK.json").exists():
        report = json.loads(Path("docs/OLLAMA_SAMPLE_BENCHMARK.json").read_text(encoding="utf-8"))
        report["models"] = [entry for entry in report["models"] if entry["model"] not in selected]
    settings = load_settings()
    for model in selected:
        print(f"Benchmarking {model}", flush=True)
        with tempfile.TemporaryDirectory(prefix="slide-navigator-sample-") as directory:
            root = Path(directory)
            isolated = replace(settings, data_dir=root, database_path=root / "db.sqlite3", imports_dir=root / "imports",
                thumbnails_dir=root / "thumbnails", indexes_dir=root / "indexes", staging_dir=root / "staging", rendering_enabled=False)
            with TestClient(create_app(isolated)) as client:
                uploads = []
                for name, slides in SAMPLES.items():
                    deck = Presentation(); deck.core_properties.title = f"{name} review"
                    for text in slides:
                        slide = deck.slides.add_slide(deck.slide_layouts[6])
                        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(3)).text = text
                    data = io.BytesIO(); deck.save(data)
                    uploads.append(("files", (name + ".pptx", data.getvalue())))
                uploaded = client.post("/api/import", files=uploads).json()
                imported = client.get(f"/api/import/jobs/{uploaded['job_id']}").json()
                with sqlite3.connect(isolated.database_path) as db:
                    db.executemany("INSERT INTO tags (id, name, normalized_name, tag_type, created_at) VALUES (?, ?, ?, ?, ?)",
                        [(key, name, name.lower(), category, datetime.now(timezone.utc).isoformat()) for key, name, category in [
                            ("proposal", "Proposal", "deck_type"), ("architecture", "Architecture", "deck_type"),
                            ("summary", "Summary", "slide_type"), ("approach", "Approach", "slide_type"),
                            ("operations", "Operations", "topic"), ("cloud", "Cloud", "topic")]])
                db.close()
                started = time.perf_counter()
                submitted = client.post("/api/enrichment/jobs", json={"all_decks": True, "model": model, "context_length": 8192, "concurrency": 1})
                submitted.raise_for_status()
                job = client.get(f"/api/enrichment/jobs/{submitted.json()['job_id']}").json()
                deck_names = {item["deck_id"]: item["source_name"] for item in imported["items"]}
                results = []
                for item in job["items"]:
                    if not item["result_id"]:
                        results.append({"sample": deck_names[item["deck_id"]], "kind": item["entity_type"], "error": item["error"]})
                        continue
                    suggestions = client.get(f"/api/enrichment/{item['entity_type']}/{item['entity_id']}/suggestions").json()["items"]
                    value = next(row for row in suggestions if row["id"] == item["result_id"])
                    results.append({"sample": deck_names[item["deck_id"]], "kind": item["entity_type"],
                        "wall_seconds": (datetime.fromisoformat(item["completed_at"]) - datetime.fromisoformat(item["started_at"])).total_seconds(),
                        "suggestion": value["content"]["suggestion"], "metrics": value["content"]["metrics"]})
                slide_results = [r for r in results if r["kind"] == "slide" and "wall_seconds" in r]
                generated_slides = [r for r in slide_results if r["metrics"]]
                entry = {"model": model, "wall_seconds": time.perf_counter() - started, "status": job["status"], "counts": job["counts"],
                    "mean_seconds_per_slide": sum(r["wall_seconds"] for r in slide_results) / len(slide_results) if slide_results else None,
                    "mean_seconds_per_model_analysed_slide": sum(r["wall_seconds"] for r in generated_slides) / len(generated_slides) if generated_slides else None,
                    "results": results}
                report["models"].append(entry)
                Path("docs/OLLAMA_SAMPLE_BENCHMARK.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({key: value for key, value in entry.items() if key != "results"}), flush=True)


if __name__ == "__main__":
    main()
