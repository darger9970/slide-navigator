import asyncio
import io
import sqlite3
import time
import zipfile
from fastapi import UploadFile
from fastapi.testclient import TestClient
import pytest
from app.main import create_app
from app import ingestion
from app.database import connect_database
from test_ingestion import create_presentation,make_settings


class SimulatedCrash(BaseException):pass


def stage(settings,filename,content):
    return asyncio.run(ingestion.stage_browser_uploads(settings,[UploadFile(io.BytesIO(content),filename=filename)],[{}],'managed_copy'))


def wait_job(client,job):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=client.get('/api/import/jobs/'+job).json()
        if result['status'] in ('completed','completed_with_errors','failed'):return result
        time.sleep(.03)
    pytest.fail('Recovered import did not finish')


def test_restart_after_deck_commit_resumes_without_duplicate_or_metadata_loss(tmp_path,monkeypatch):
    settings=make_settings(tmp_path)
    with TestClient(create_app(settings)):pass
    job=stage(settings,'Resume.pptx',create_presentation('Resume'))
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE import_jobs SET status='processing',started_at='2026-11-17T09:00:00+00:00' WHERE id=?",(job,))
        db.execute("UPDATE import_job_items SET status='processing' WHERE job_id=?",(job,))
        item=db.execute('SELECT * FROM import_job_items WHERE job_id=?',(job,)).fetchone()
    original=ingestion._render_deck
    def crash(*args,**kwargs):raise SimulatedCrash()
    monkeypatch.setattr(ingestion,'_render_deck',crash)
    with pytest.raises(SimulatedCrash):ingestion._process_presentation_item(settings,job,item,'managed_copy')
    with connect_database(settings.database_path) as db:
        deck_id=db.execute('SELECT deck_id FROM import_job_items WHERE job_id=?',(job,)).fetchone()[0]
        assert deck_id
    ingestion.update_deck_metadata(settings,deck_id,{'description':'Keep this manual edit'})
    monkeypatch.setattr(ingestion,'_render_deck',original)
    with TestClient(create_app(settings)) as client:
        result=wait_job(client,job)
        assert result['status']=='completed' and result['imported_count']==1 and result['duplicate_count']==0
        assert result['started_at']=='2026-11-17T09:00:00+00:00'
        assert client.get('/api/decks').json()['total']==1
        deck=client.get('/api/decks/'+deck_id).json()
        assert deck['description']=='Keep this manual edit' and deck['slide_count']==2
        ingestion.process_import_job(settings,job)
        assert client.get('/api/import/jobs/'+job).json()['processed_items']==1


def test_archive_discovery_crash_rolls_back_children_then_recovers(tmp_path,monkeypatch):
    settings=make_settings(tmp_path)
    with TestClient(create_app(settings)):pass
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as z:
        z.writestr('a.pptx',create_presentation('A'));z.writestr('folder/b.pptx',create_presentation('B'));z.writestr('notes.txt','Skip')
    job=stage(settings,'batch.zip',content.getvalue())
    with connect_database(settings.database_path) as db:item=db.execute('SELECT * FROM import_job_items WHERE job_id=?',(job,)).fetchone()
    original=ingestion._insert_job_item;calls=0
    def crash(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise SimulatedCrash()
        return original(*args,**kwargs)
    monkeypatch.setattr(ingestion,'_insert_job_item',crash)
    with pytest.raises(SimulatedCrash):ingestion._extract_archive_presentations(settings,job,item)
    with connect_database(settings.database_path) as db:
        assert db.execute('SELECT COUNT(*) FROM import_job_items WHERE job_id=?',(job,)).fetchone()[0]==1
        assert db.execute('SELECT total_items FROM import_jobs WHERE id=?',(job,)).fetchone()[0]==1
    monkeypatch.setattr(ingestion,'_insert_job_item',original)
    with TestClient(create_app(settings)) as client:
        result=wait_job(client,job)
        assert result['status']=='completed' and result['imported_count']==2
        assert result['items_total']==4 and result['processed_items']==4
        assert client.get('/api/decks').json()['total']==2
        assert client.get('/api/slides').json()['total']==4


def test_recovery_does_not_reclaim_a_live_worker(tmp_path):
    settings=make_settings(tmp_path)
    with TestClient(create_app(settings)):pass
    job=stage(settings,'Live.pptx',create_presentation('Live'))
    key=(str(settings.database_path.resolve()),job)
    with ingestion.IMPORT_GUARD:ingestion.ACTIVE_IMPORTS.add(key)
    try:assert ingestion.recover_imports(settings)==[]
    finally:
        with ingestion.IMPORT_GUARD:ingestion.ACTIVE_IMPORTS.discard(key)
    assert ingestion.recover_imports(settings)==[job]


def test_recovery_does_not_resurrect_a_deleted_deck(tmp_path,monkeypatch):
    from app.library_management import deletion
    settings=make_settings(tmp_path)
    with TestClient(create_app(settings)):pass
    job=stage(settings,'Deleted.pptx',create_presentation('Deleted'))
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE import_jobs SET status='processing' WHERE id=?",(job,))
        db.execute("UPDATE import_job_items SET status='processing' WHERE job_id=?",(job,))
        item=db.execute('SELECT * FROM import_job_items WHERE job_id=?',(job,)).fetchone()
    original=ingestion._render_deck
    def crash(*args,**kwargs):raise SimulatedCrash()
    monkeypatch.setattr(ingestion,'_render_deck',crash)
    with pytest.raises(SimulatedCrash):ingestion._process_presentation_item(settings,job,item,'managed_copy')
    with connect_database(settings.database_path) as db:deck_id=db.execute('SELECT deck_id FROM import_job_items WHERE job_id=?',(job,)).fetchone()[0]
    deletion(settings,'decks',[deck_id],execute=True,expected_slide_count=2)
    monkeypatch.setattr(ingestion,'_render_deck',original)
    with TestClient(create_app(settings)) as client:
        result=wait_job(client,job)
        assert result['status']=='completed_with_errors'
        assert 'removed from the library' in result['items'][0]['message']
        assert client.get('/api/decks').json()['total']==0
