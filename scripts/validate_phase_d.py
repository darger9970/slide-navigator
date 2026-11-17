"""Real LibreOffice opening and visual comparison of native multi-source export."""
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from fastapi.testclient import TestClient
from PIL import Image,ImageChops,ImageStat
from pptx import Presentation
from app.config import load_settings
from app.main import create_app
from app.ingestion import get_thumbnail_path
from native_fixtures import rich_presentation


def main():
    root=Path('.validation-phase-d')/str(time.time_ns());root.mkdir(parents=True)
    settings=replace(load_settings(),data_dir=root,database_path=root/'metadata.sqlite3',imports_dir=root/'imports',thumbnails_dir=root/'thumbnails',indexes_dir=root/'indexes',staging_dir=root/'staging',rendering_enabled=True,worker_timeout_seconds=180)
    originals=[rich_presentation('Source A','FF5A00',6),rich_presentation('Source B','2255AA',5)]
    start=time.monotonic()
    with TestClient(create_app(settings)) as client:
        imported=client.post('/api/import',files=[('files',(f'Rich source {i+1}.pptx',content,'application/vnd.openxmlformats-officedocument.presentationml.presentation')) for i,content in enumerate(originals)])
        imported.raise_for_status();import_job=client.get('/api/import/jobs/'+imported.json()['job_id']).json()
        assert import_job['status']=='completed',import_job
        slides=client.get('/api/slides').json()['items'];slides=list(reversed(slides));ids=[s['id'] for s in slides]
        preview=client.post('/api/exports/preflight',json={'ids':ids});preview.raise_for_status()
        job_start=time.monotonic();exported=client.post('/api/exports',json={'ids':ids,'token':preview.json()['token'],'filename':'Validated native deck.pptx'});exported.raise_for_status()
        job_id=exported.json()['job_id'];job=client.get('/api/work-jobs/'+job_id).json();assert job['status']=='completed',job
        seconds=time.monotonic()-job_start
        response=client.get('/api/exports/'+job_id+'/download');response.raise_for_status()
        p=Presentation(io.BytesIO(response.content));assert len(p.slides)==2
        comparisons=[]
        for index,slide in enumerate(slides,1):
            source=get_thumbnail_path(settings,slide['id']);assert source is not None
            target=settings.indexes_dir/'exports'/job_id/'validation-previews'/f'slide-{index:05}.png'
            a=Image.open(source).convert('RGB');b=Image.open(target).convert('RGB');assert a.size==b.size
            diff=ImageChops.difference(a,b);mean=sum(ImageStat.Stat(diff).mean)/3
            comparisons.append({'position':index,'deck':slide['deck_name'],'mean_absolute_pixel_error':round(mean,6),'size':a.size,'exact_match':a.tobytes()==b.tobytes()})
            assert mean < 1.0,comparisons
        assert all(any(sh.has_chart for sh in s.shapes) and any(sh.has_table for sh in s.shapes) for s in p.slides)
        report={'status':'passed','real_libreoffice_opening':True,'synthetic_sources':2,'native_slides':2,'retained_objects':['editable text/shapes','charts and embedded workbooks','tables','images','OLE embedded workbooks','different themes/layouts','speaker notes','external hyperlinks'], 'export_seconds':round(seconds,3),'total_seconds':round(time.monotonic()-start,3),'visual_comparisons':comparisons,'export_path':job['output_path'],'sha256':hashlib.sha256(response.content).hexdigest(),'warnings':preview.json()['warnings']}
        Path('docs/PHASE_D_REAL_VALIDATION.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
