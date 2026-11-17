"""Explicit local-model validation on synthetic telecom content, isolated from the library."""
import io
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
from app.config import load_settings
from app.main import create_app


def main():
    settings = load_settings()
    with tempfile.TemporaryDirectory(prefix="slide-navigator-phase-a-") as directory:
        root = Path(directory)
        settings = replace(settings, data_dir=root, database_path=root / "db.sqlite3", imports_dir=root / "imports",
            thumbnails_dir=root / "thumbnails", indexes_dir=root / "indexes", staging_dir=root / "staging", rendering_enabled=False)
        deck = Presentation(); deck.core_properties.title = "Northwind telecom quarterly review"
        for text in [
            "Northwind operates in the telecom industry. The purpose of this presentation is to review quarterly performance. Revenue increased by 12 percent. Customer retention was stable. Regional results varied and require further analysis. No future revenue target or commercial guarantee is given.",
            "Northwind next steps: review regional performance monthly, document customer feedback, and validate the reporting pack with Finance. Operations owns reporting. No new budget, staffing commitment, deployment date or workshop has been approved. These actions remain proposals for human review.",
        ]:
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(3)).text = text
        data = io.BytesIO(); deck.save(data)
        with TestClient(create_app(settings)) as client:
            submitted = client.post("/api/import", files=[("files", ("phase-a.pptx", data.getvalue()))]).json()
            deck_id = client.get(f"/api/import/jobs/{submitted['job_id']}").json()["items"][0]["deck_id"]
            started = time.perf_counter()
            response = client.post("/api/enrichment/jobs", json={"deck_ids": [deck_id], "model": settings.enrichment_model, "retries": 0})
            response.raise_for_status()
            job = client.get(f"/api/enrichment/jobs/{response.json()['job_id']}").json()
            outcomes = []
            for item in job["items"]:
                if item["result_id"]:
                    result = client.get(f"/api/enrichment/{item['entity_type']}/{item['entity_id']}/suggestions").json()["items"][0]
                    outcomes.append({"kind": item["entity_type"], "suggestion": result["content"]["suggestion"], "metrics": result["content"]["metrics"]})
            saved = client.get(f"/api/decks/{deck_id}").json()
            report = {"model": job["model"], "wall_seconds": time.perf_counter() - started, "status": job["status"],
                "counts": job["counts"], "items": job["items"], "suggestions": outcomes,
                "existing_title_unchanged": saved["name"] == "Northwind telecom quarterly review", "metadata_not_accepted": saved["keywords"] == [] and saved["purpose"] is None}
            Path("docs/PHASE_A_REAL_VALIDATION.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k not in {"items", "suggestions"}}), flush=True)
            assert job["status"] == "completed" and report["existing_title_unchanged"] and report["metadata_not_accepted"], report


if __name__ == "__main__": main()
