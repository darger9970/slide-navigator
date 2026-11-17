import math
import sqlite3
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app import search, work_jobs
from app.main import create_app
from test_library import library  # noqa: F401
from test_library_browser import page, browser_process  # noqa: F401


@pytest.fixture
def embeddings(monkeypatch):
    calls=[]
    def fake(settings,text,query=False):
        calls.append((text,query))
        return search.unit([1,0.05] if any(w in text.casefold() for w in ('revenue','profit','income')) else [0.05,1])
    monkeypatch.setattr(search,'embed',fake)
    return calls


def test_exact_search_fields_filters_and_empty(library):
    client,settings,decks,slides=library
    assert client.get('/api/search',params={'mode':'keyword','q':'revenue'}).json()['total']==3
    client.patch(f"/api/decks/{decks['alpha']}",json={'description':'Telecom transformation','industry':'Telecommunications','keywords':['Agentic'],'year':2025,'organisation':'ExampleCo'})
    for query in ('Telecom','Agentic','ExampleCo','2025','Discuss the revenue trend','Cloud'):
        assert client.get('/api/search',params={'mode':'keyword','q':query}).json()['total'] > 0
    assert client.get('/api/search',params=[('mode','keyword'),('q','revenue'),('tag_id','agenda'),('tag_id','cloud')]).json()['total']==1
    assert client.get('/api/search',params=[('mode','keyword'),('tag_id','agenda'),('tag_id','team')]).json()['total']==3
    assert client.get('/api/search',params={'mode':'keyword','q':'missingxyz'}).json()['items']==[]


def test_semantic_hybrid_incremental_edits_deletion_and_restart(library,embeddings):
    client,settings,decks,slides=library
    first=client.post('/api/search/index').json()['job_id']
    job=client.get(f'/api/work-jobs/{first}').json()
    assert job['status']=='completed' and job['completed']==9 and job['progress_percent']==100
    assert len(embeddings)==9
    again=client.post('/api/search/index').json()['job_id']
    assert client.get(f'/api/work-jobs/{again}').json()['skipped']==9 and len(embeddings)==9
    result=client.get('/api/search',params={'mode':'semantic','q':'profit','kind':'slide'}).json()
    assert result['items'][0]['slide_number']==1
    hybrid=client.get('/api/search',params={'q':'profit'}).json()
    assert hybrid['items'] and 'percentages' in hybrid['ranking']
    selected=slides['alpha'][0]['id']
    client.patch(f'/api/slides/{selected}',json={'description':'Updated current text'})
    stale=client.get('/api/search',params={'q':'income','mode':'semantic'}).json()
    assert selected not in [item['id'] for item in stale['items']]
    before=len(embeddings)
    third=client.post('/api/search/index').json()['job_id']
    assert client.get(f'/api/work-jobs/{third}').json()['completed']==1
    assert len(embeddings)==before+1
    assert client.post('/api/library/slides/delete',json={'ids':[selected],'expected_slide_count':1}).status_code==200
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM search_vectors WHERE entity_id=?',(selected,)).fetchone()[0]==0
    with TestClient(create_app(settings)) as restarted:
        response=restarted.get('/api/search',params={'q':'profit','mode':'semantic'}).json()
        assert selected not in [item['id'] for item in response['items']]
        assert response['pending_index_count']==0


def test_partial_failure_cancel_recovery_and_terminal_elapsed(library,monkeypatch):
    client,settings,_,_=library
    def fail(*args,**kwargs): raise ValueError('Test model unavailable')
    monkeypatch.setattr(search,'embed',fail)
    job=work_jobs.create(settings,'index')['job_id'];search.run_index(settings,job)
    failed=work_jobs.get(settings,job)
    assert failed['status']=='completed_with_errors' and failed['failed']==9
    assert work_jobs.get(settings,job)['elapsed_seconds']==failed['elapsed_seconds']
    job=work_jobs.create(settings,'index')['job_id']
    assert work_jobs.create(settings,'index')['reused']
    client.post(f'/api/work-jobs/{job}/cancel');search.run_index(settings,job)
    assert work_jobs.get(settings,job)['status']=='cancelled'
    work_jobs.recover(settings)
    assert work_jobs.get(settings,job)['status']=='cancelled'
    job=work_jobs.create(settings,'index')['job_id']
    work_jobs.update(settings,job,status='processing',started_at=work_jobs.now())
    assert {'id':job,'kind':'index'} in work_jobs.recover(settings)
    fallback=client.get('/api/search',params={'q':'revenue'}).json()
    assert fallback['items'] and 'unavailable' in fallback['warning']
    assert client.get('/api/search',params={'q':'profit','mode':'semantic'}).status_code==503


def test_embedding_protocol_chunking_and_validation(library,monkeypatch):
    _,settings,_,_=library
    requests=[]
    class Client:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,json):
            requests.append(json)
            return httpx.Response(200,json={'embeddings':[[3,4]]},request=httpx.Request('POST',url))
    monkeypatch.setattr(search.httpx,'Client',Client)
    vector=search.embed(settings,'é'*1500)
    assert len(requests)==3 and vector==pytest.approx([.6,.8])
    assert all(r['truncate'] is False and r['input'].startswith('search_document: ') for r in requests)
    search.embed(settings,'finance',True);assert requests[-1]['input'].startswith('search_query: ')
    for invalid in ([],[0,0],[math.nan,1]):
        with pytest.raises(ValueError): search.unit(invalid)


def test_search_selection_survives_browser_refresh(page):
    page.click('[data-page="search"]')
    page.change('search-mode','keyword')
    page.evaluate('document.getElementById("search-page-input").value="revenue";document.getElementById("search-page-form").requestSubmit()')
    page.wait('document.querySelectorAll("#search-results [data-select-slide]").length === 3')
    page.click('#search-results [data-select-slide]')
    page.wait('selectedSlides.size === 1')
    selected=page.evaluate('selectedSlides.values()[0].id')
    page.command('Page.reload')
    page.wait(f'selectedSlides.size === 1 && selectedSlides.has({json.dumps(selected)})')
    page.click('[data-page="slides"]')
    page.wait('document.querySelectorAll("#slide-grid .slide-card").length === 6')
    assert page.evaluate('document.getElementById("create-deck").textContent')=='Create Deck (1)'
    assert not page.errors
