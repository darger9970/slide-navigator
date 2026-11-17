"""Separate real Ollama acceptance, synthetic local documents only."""
import io
import json
import sys
import time
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pptx import Presentation
from pptx.util import Inches
from fastapi.testclient import TestClient
from app.config import load_settings
from app.main import create_app


def main():
    root=Path('.validation-phase-c')/str(time.time_ns());root.mkdir(parents=True)
    base=load_settings()
    settings=replace(base,data_dir=root,database_path=root/'metadata.sqlite3',imports_dir=root/'imports',thumbnails_dir=root/'thumbnails',indexes_dir=root/'indexes',staging_dir=root/'staging',rendering_enabled=False)
    cases=[('Telecom transformation','Billing automation detects revenue leakage in telecommunications networks. Artificial intelligence improves carrier assurance.','telecommunications billing revenue leakage'),
           ('Cloud architecture','Agentic AI architecture uses Microsoft Azure containers, orchestration, tool execution and retrieval augmented generation.','Azure agent architecture'),
           ('Operating model','An operating model transformation aligns people, organisational capabilities, governance, roles and processes.','organisational governance transformation')]
    files=[]
    for title,text,query in cases:
        p=Presentation();p.core_properties.title=title
        slide=p.slides.add_slide(p.slide_layouts[6]);slide.shapes.add_textbox(Inches(1),Inches(1),Inches(8),Inches(3)).text=text
        stream=io.BytesIO();p.save(stream);files.append(('files',(title+'.pptx',stream.getvalue(),'application/vnd.openxmlformats-officedocument.presentationml.presentation')))
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/import',files=files);response.raise_for_status()
        start=time.monotonic();job_id=client.post('/api/search/index').json()['job_id'];seconds=time.monotonic()-start
        job=client.get(f'/api/work-jobs/{job_id}').json()
        results=[]
        for title,text,query in cases:
            start=time.monotonic();response=client.get('/api/search',params={'q':query,'mode':'semantic','kind':'slide'});response.raise_for_status();data=response.json()
            results.append({'query':query,'expected':title,'top_deck':data['items'][0]['deck_name'] if data['items'] else None,'seconds':round(time.monotonic()-start,3)})
        import sqlite3
        with sqlite3.connect(settings.database_path) as db: dims=sorted({r[0] for r in db.execute('SELECT dimension FROM search_vectors')})
    report={'model':settings.embedding_model,'sample':'Three synthetic presentations, one slide each; no user content sent externally.','index_seconds':round(seconds,3),'seconds_per_document':round(seconds/6,3),'dimensions':dims,'status':job['status'],'completed':job['completed'],'failed':job['failed'],'queries':results,'all_expected_top_ranked':all(r['expected']==r['top_deck'] for r in results)}
    Path('docs/PHASE_C_REAL_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    if job['status']!='completed' or not report['all_expected_top_ranked']:raise SystemExit(1)


if __name__=='__main__':main()
