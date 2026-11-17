import sqlite3
from app.database import initialize_database
from test_library import library  # noqa: F401
from test_library_browser import page, browser_process  # noqa: F401


def test_registry_scope_aliases_bulk_and_persistence(library):
    client, settings, decks, slides = library
    assert len(client.get('/api/taxonomy').json()['categories']) == 6
    assert client.post('/api/tags', json={'name':'Invalid','tag_type':'unknown'}).status_code == 422
    category={'id':'region','label':'Region','allow_deck':True,'allow_slide':False}
    assert client.post('/api/taxonomy/categories',json=category).status_code == 200
    tag=client.post('/api/tags',json={'name':'Europe','tag_type':'region'}).json()
    bulk={'kind':'deck','ids':[decks['alpha'],decks['beta']],'tag_ids':[tag['id']]}
    assert client.post('/api/taxonomy/bulk',json=bulk).json()['updated'] == 2
    assert client.get('/api/decks',params={'tag_id':tag['id']}).json()['total'] == 2
    assert client.put(f"/api/slides/{slides['alpha'][0]['id']}/tags/{tag['id']}").status_code == 422
    assert client.patch(f"/api/tags/{tag['id']}",json={'name':'European market'}).status_code == 200
    assert client.post('/api/tags',json={'name':'Europe','tag_type':'region'}).json()['id'] == tag['id']
    assert client.get('/api/taxonomy',params={'q':'Europe'}).json()['items'][0]['deck_count'] == 2
    assert client.patch(f"/api/tags/{tag['id']}",json={'status':'archived'}).status_code == 200
    bulk['ids']=[decks['untagged']]
    assert client.post('/api/taxonomy/bulk',json=bulk).status_code == 422
    initialize_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute('SELECT count(*) FROM deck_tags WHERE tag_id=?',(tag['id'],)).fetchone()[0] == 2


def test_merge_delete_requires_current_impact_and_is_atomic(library):
    client,settings,decks,slides=library
    request={'action':'merge','target_id':'unused'}
    preview=client.post('/api/tags/cloud/impact',json=request)
    assert preview.status_code == 200
    assert preview.json()['slide_count'] > 0
    client.put(f"/api/slides/{slides['untagged'][0]['id']}/tags/cloud")
    assert client.post('/api/tags/cloud/apply',json={**request,'token':preview.json()['token']}).status_code == 409
    current=client.post('/api/tags/cloud/impact',json=request).json()
    assert client.post('/api/tags/cloud/apply',json={**request,'token':current['token']}).status_code == 200
    assert not client.get('/api/taxonomy',params={'q':'Cloud'}).json()['items'][0]['id'] == 'cloud'
    assert client.post('/api/tags',json={'name':'Cloud','tag_type':'topic'}).json()['id'] == 'unused'
    delete={'action':'delete'}
    impact=client.post('/api/tags/unused/impact',json=delete).json()
    assert client.post('/api/tags/unused/apply',json={**delete,'token':impact['token']}).status_code == 200
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.execute("SELECT count(*) FROM taxonomy_history WHERE action IN ('merge','delete')").fetchone()[0] == 2


def test_bulk_rolls_back_if_any_entity_missing(library):
    client,settings,decks,_=library
    response=client.post('/api/taxonomy/bulk',json={'kind':'deck','ids':[decks['untagged'],'missing'],'tag_ids':['proposal']})
    assert response.status_code == 404
    assert client.get(f"/api/decks/{decks['untagged']}").json()['tags'] == []


def test_tag_manager_browser_create_and_archive(page):
    page.click('[data-page="tags"]')
    page.wait('document.querySelectorAll("#taxonomy-items article").length === 7')
    page.evaluate('document.querySelector(".taxonomy-create input").value="Browser industry"; document.getElementById("taxonomy-category").value="industry"; document.querySelector(".taxonomy-create").requestSubmit()')
    page.wait('document.getElementById("taxonomy-items").textContent.includes("Browser industry")')
    page.evaluate('Array.from(document.querySelectorAll("#taxonomy-items > article")).find(e => e.textContent.includes("Browser industry")).querySelectorAll("button")[1].click()')
    page.wait('document.getElementById("taxonomy-items").textContent.includes("archived") && document.getElementById("taxonomy-items").textContent.includes("Restore")')
    assert not page.errors


def test_populated_v10_migration_keeps_ids_assignments_aliases_and_backup(library):
    client,settings,decks,slides=library
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO tag_aliases VALUES ('old-alias','cloud','Cloud platforms','cloud platforms')")
        db.execute("UPDATE app_metadata SET value='10' WHERE key='schema_version'")
        before={table: db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall() for table in ['tags','deck_tags','slide_tags','tag_aliases']}
        db.commit()
        db.execute('PRAGMA foreign_keys=OFF')
        db.executescript("CREATE TABLE old_tags(id TEXT PRIMARY KEY,tag_type TEXT NOT NULL CHECK(tag_type IN ('deck_type','slide_type','topic')),name TEXT NOT NULL,normalized_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,UNIQUE(tag_type,normalized_name)); INSERT INTO old_tags SELECT * FROM tags; DROP TABLE tags; ALTER TABLE old_tags RENAME TO tags;")
    initialize_database(settings.database_path)
    assert settings.database_path.with_name(settings.database_path.name+'.before-v11.sqlite3').exists()
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        for table,rows in before.items():
            assert db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall() == rows
