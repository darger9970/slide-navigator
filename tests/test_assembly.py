import copy
import io
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path
import pytest
from pptx import Presentation
from fastapi.testclient import TestClient
from app.main import create_app
from app import assembly, work_jobs
from test_library import library  # noqa: F401
from test_library_browser import page, browser_process  # noqa: F401
from test_ingestion import make_settings,post_files
from native_fixtures import rich_presentation


def export(client,ids,**values):
    preview=client.post('/api/exports/preflight',json={'ids':ids})
    assert preview.status_code==200,preview.text
    response=client.post('/api/exports',json={'ids':ids,'token':preview.json()['token'],**values})
    assert response.status_code==202,response.text
    return response.json()['job_id']


def test_native_multisource_order_notes_tables_and_source_preservation(library):
    client,settings,decks,slides=library
    with sqlite3.connect(settings.database_path) as db:
        paths=[Path(r[0]) for r in db.execute('SELECT stored_path FROM decks')]
    originals={p:p.read_bytes() for p in paths}
    ids=[slides['beta'][1]['id'],slides['alpha'][0]['id'],slides['beta'][0]['id']]
    job_id=export(client,ids,filename='Native test.pptx')
    job=client.get(f'/api/work-jobs/{job_id}').json()
    assert job['status']=='completed',job
    assert job['processed']==3 and job['progress_percent']==100
    response=client.get(f'/api/exports/{job_id}/download');assert response.status_code==200
    presentation=Presentation(io.BytesIO(response.content))
    assert len(presentation.slides)==3
    assert presentation.slides[0].shapes[0].text=='Next steps'
    assert any(s.has_table for s in presentation.slides[1].shapes)
    assert presentation.slides[1].notes_slide.notes_text_frame.text=='Discuss the revenue trend.'
    assert len(presentation.slide_masters)==2
    assert len(presentation.slides[1].shapes)>1
    assert all(p.read_bytes()==content for p,content in originals.items())
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f'/api/exports/{job_id}/download').content==response.content
        assert restarted.get(f'/api/work-jobs/{job_id}').json()['elapsed_seconds']==job['elapsed_seconds']


def test_export_replay_validation_and_deleted_selection(library):
    client,settings,decks,slides=library
    ids=[slides['alpha'][0]['id']]
    preview=client.post('/api/exports/preflight',json={'ids':ids}).json()
    value={'ids':ids,'token':preview['token'],'filename':'Download.pptx','request_id':str(uuid.uuid4())}
    first=client.post('/api/exports',json=value).json()
    assert client.post('/api/exports',json=value).json()['job_id']==first['job_id']
    assert client.post('/api/exports',json={**value,'filename':'Different.pptx'}).status_code==409
    assert client.post('/api/exports',json={**value,'request_id':str(uuid.uuid4()),'filename':'../bad.pptx'}).status_code==422
    assert client.post('/api/exports/preflight',json={'ids':ids*2}).status_code==422
    client.post('/api/library/slides/delete',json={'ids':ids,'expected_slide_count':1})
    assert client.post('/api/exports/preflight',json={'ids':ids}).status_code==422
    result=client.post('/api/exports',json={**value,'request_id':str(uuid.uuid4())}).json()
    failed=client.get(f"/api/work-jobs/{result['job_id']}").json()
    assert failed['status']=='failed' and 'deleted' in failed['error']
    assert client.get(f"/api/exports/{result['job_id']}/download").status_code==409


def mutate_source(settings,deck_id,transform):
    with sqlite3.connect(settings.database_path) as db:path=Path(db.execute('SELECT stored_path FROM decks WHERE id=?',(deck_id,)).fetchone()[0])
    with zipfile.ZipFile(path) as package: parts={n:package.read(n) for n in package.namelist()}
    transform(parts)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as package:
        for n,b in parts.items():package.writestr(n,b)


def test_mixed_dimensions_unsupported_content_missing_targets_and_changed_source_fail(library):
    client,settings,decks,slides=library
    ids=[slides['alpha'][0]['id'],slides['beta'][0]['id']]
    preview=client.post('/api/exports/preflight',json={'ids':ids}).json()
    def resize(parts):
        root=assembly.xml(parts['ppt/presentation.xml']);root.find('p:sldSz',assembly.NS).set('cx','123456');parts['ppt/presentation.xml']=assembly.encode(root)
    mutate_source(settings,decks['beta'],resize)
    response=client.post('/api/exports/preflight',json={'ids':ids})
    assert response.status_code==422 and 'different slide sizes' in response.text
    result=client.post('/api/exports',json={'ids':ids,'token':preview['token']}).json()
    assert client.get(f"/api/work-jobs/{result['job_id']}").json()['status']=='failed'
    def timing(parts):
        root=assembly.xml(parts['ppt/slides/slide1.xml']);assembly.ET.SubElement(root,'{'+assembly.P+'}timing');parts['ppt/slides/slide1.xml']=assembly.encode(root)
    mutate_source(settings,decks['alpha'],timing)
    response=client.post('/api/exports/preflight',json={'ids':[ids[0]]})
    assert response.status_code==422 and 'Animation' in response.text


def test_browser_deck_selection_export_and_refresh(page):
    page.click('#deck-table-body td:first-child > .text-button')
    page.wait('document.querySelectorAll("#deck-slides-list [data-select-slide]").length===2')
    page.click('#deck-slides-list [data-select-slide]')
    page.click('#deck-dialog-close');page.click('#create-deck');page.click('#confirm-slide-order')
    page.wait('document.getElementById("export-filename") !== null')
    page.evaluate('window.showSaveFilePicker=undefined')
    page.evaluate('Array.from(document.querySelectorAll(".native-builder button")).find(b=>b.textContent==="Check native export").click()')
    page.wait('document.getElementById("export-check").textContent.includes("Generate PowerPoint")')
    page.evaluate('Array.from(document.querySelectorAll("#export-check button")).find(b=>b.textContent==="Generate PowerPoint").click()')
    page.wait('document.getElementById("export-progress").textContent.includes("Save PowerPoint")')
    page.command('Page.reload')
    page.wait('selectedSlides.size===1')
    page.click('[data-page="builder"]')
    page.wait('document.getElementById("export-progress").textContent.includes("Save PowerPoint")')


def test_charts_embedded_workbooks_objects_images_and_different_themes(tmp_path):
    settings=make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        post_files(client,[('nativeA.pptx',rich_presentation()),('nativeB.pptx',rich_presentation('Source B','2255AA',5))])
        rows=client.get('/api/slides').json()['items'];ids=[r['id'] for r in reversed(rows)]
        job=export(client,ids)
        result=client.get(f'/api/work-jobs/{job}').json();assert result['status']=='completed',result['error']
        output=client.get(f'/api/exports/{job}/download').content
        presentation=Presentation(io.BytesIO(output))
        assert len(presentation.slides)==2 and len(presentation.slide_masters)==2
        for slide in presentation.slides:
            assert any(shape.has_chart for shape in slide.shapes)
            assert any(shape.has_table for shape in slide.shapes)
            assert any('oleObj' in shape._element.xml for shape in slide.shapes)
            assert 'Native speaker notes' in slide.notes_slide.notes_text_frame.text
        with zipfile.ZipFile(io.BytesIO(output)) as package:
            embeddings=[n for n in package.namelist() if '/embeddings/' in n]
            assert len(embeddings)>=4
            assert any('https://example.com/reference' in package.read(n).decode() for n in package.namelist() if n.endswith('.rels'))


def test_missing_dependency_and_unsupported_relationship_are_explicit(library):
    client,settings,decks,slides=library
    def missing(parts):parts.pop('ppt/slideLayouts/slideLayout7.xml')
    mutate_source(settings,decks['alpha'],missing)
    response=client.post('/api/exports/preflight',json={'ids':[slides['alpha'][0]['id']]})
    assert response.status_code==422 and 'Missing package dependency' in response.text
    def unknown(parts):
        name='ppt/slides/_rels/slide1.xml.rels';root=assembly.xml(parts[name]);assembly.ET.SubElement(root,'{'+assembly.PKG+'}Relationship',Id='rId999',Type=assembly.R+'/unsupportedWidget',Target='../widget.bin');parts[name]=assembly.encode(root)
    mutate_source(settings,decks['beta'],unknown)
    response=client.post('/api/exports/preflight',json={'ids':[slides['beta'][0]['id']]})
    assert response.status_code==422 and 'unsupportedWidget' in response.text


def test_changed_source_token_cancellation_and_recovery(library,monkeypatch):
    client,settings,decks,slides=library
    ids=[slides['alpha'][0]['id']]
    preview=client.post('/api/exports/preflight',json={'ids':ids}).json()
    def change_property(parts):parts['docProps/core.xml']=parts['docProps/core.xml'].replace(b'Alpha',b'Changed')
    mutate_source(settings,decks['alpha'],change_property)
    result=client.post('/api/exports',json={'ids':ids,'token':preview['token']}).json()
    assert 'changed since review' in client.get(f"/api/work-jobs/{result['job_id']}").json()['error']
    preview=client.post('/api/exports/preflight',json={'ids':ids}).json()
    request=assembly.ExportRequest(ids=ids,token=preview['token'])
    job=assembly.create_job(settings,request)['job_id']
    work_jobs.update(settings,job,status='processing')
    assert {'id':job,'kind':'export'} in work_jobs.recover(settings)
    original=assembly.Composer.write
    def cancel_after_write(self,path):
        original(self,path)
        work_jobs.update(settings,job,status='cancel_requested')
    monkeypatch.setattr(assembly.Composer,'write',cancel_after_write)
    assembly.run_job(settings,job)
    state=work_jobs.get(settings,job)
    assert state['status']=='cancelled' and not state['output_path']
    assert not list((settings.indexes_dir/'exports'/job).glob('*.partial'))
