"""Local exact/semantic retrieval. Changed fingerprints never use stale vectors."""
import hashlib
import json
import math
import re
import struct
import threading
import time
import httpx
from fastapi import HTTPException
from app.database import connect_database
from app.library_tags import assigned_tags
from app import work_jobs
from app.enrichment import MODEL_GATE

INDEX_LOCK = threading.Lock()


def migrate(db):
    work_jobs.migrate(db)
    db.execute("""CREATE TABLE IF NOT EXISTS search_vectors (
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, model TEXT NOT NULL, dimension INTEGER NOT NULL,
        vector BLOB NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(entity_type,entity_id))""")
    db.execute("INSERT OR IGNORE INTO app_metadata VALUES ('search_revision','0')")
    for table in ('decks','slides','tags','deck_tags','slide_tags','tag_aliases','organisations'):
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone():
            continue
        for action in ('INSERT','UPDATE','DELETE'):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS search_dirty_{table}_{action} AFTER {action} ON {table}
                BEGIN UPDATE app_metadata SET value=CAST(value AS INTEGER)+1 WHERE key='search_revision'; END""")
    for kind,table in [('deck','decks'),('slide','slides')]:
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS search_delete_{kind} AFTER DELETE ON {table}
                BEGIN DELETE FROM search_vectors WHERE entity_type='{kind}' AND entity_id=OLD.id; END""")


def documents(db,kind=None,entity_id=None):
    condition = ' WHERE d.id=?' if kind=='deck' else ' WHERE d.id=(SELECT deck_id FROM slides WHERE id=?)' if kind=='slide' else ''
    decks={r['id']:dict(r) for r in db.execute('SELECT d.*,o.name AS organisation FROM decks d LEFT JOIN organisations o ON o.id=d.organisation_id'+condition,(entity_id,) if kind else ())}
    results=[]
    def append(kind,row,parent=None):
        tags=assigned_tags(db,kind,row['id'])
        inherited=parent or row
        deck_tags=assigned_tags(db,'deck',inherited['id']) if parent else []
        aliases=[r[0] for tag in tags+deck_tags for r in db.execute('SELECT alias FROM tag_aliases WHERE tag_id=?',(tag['id'],))]
        fields=[row.get(key) for key in ('name','title','description','text_content','speaker_notes','keywords','industry','purpose','subject','subtitle','author')]
        fields += [inherited.get(key) for key in ('name','description','organisation','year','industry','keywords','purpose')]
        fields += [t['name'] for t in tags+deck_tags if t['status']=='active']+aliases
        text='\n'.join(str(v) for v in fields if v is not None)
        fingerprint=hashlib.sha256(('search-v1\n'+text).encode()).hexdigest()
        result={'kind':kind,'id':row['id'],'deck_id':inherited['id'],'title':row.get('title') or row.get('name'),
                'description':row.get('description'),'text':text,'fingerprint':fingerprint,'tags':tags,
                'deck_name':inherited['name'],'original_filename':inherited['original_filename'],
                'organisation_id':inherited['organisation_id'],'organisation':inherited.get('organisation'),
                'year':inherited['year'],'slide_number':row.get('slide_number'),'internal_slide_id':row.get('internal_slide_id'),
                'title_source':row.get('title_source'),'deck_tags':deck_tags}
        if kind=='deck':
            cover=db.execute('SELECT id FROM slides WHERE deck_id=? ORDER BY slide_number LIMIT 1',(row['id'],)).fetchone()
            result['cover_slide_id']=cover[0] if cover else None
            result['name']=row['name'];result['slide_count']=row['slide_count']
        else:
            rendering=db.execute('SELECT status,updated_at FROM slide_renderings WHERE slide_id=?',(row['id'],)).fetchone()
            result['rendering_status']=rendering['status'] if rendering else 'unavailable'
            result['rendering_updated_at']=rendering['updated_at'] if rendering else None
        results.append(result)
    if kind != 'slide':
        for row in decks.values(): append('deck',row)
    if kind != 'deck':
        for row in db.execute('SELECT * FROM slides'+(' WHERE id=?' if kind=='slide' else ''),(entity_id,) if kind=='slide' else ()):
            append('slide',dict(row),decks[row['deck_id']])
    return results


def unit(vector):
    if not vector or any(not math.isfinite(v) for v in vector): raise ValueError('Invalid embedding vector.')
    length=math.sqrt(sum(v*v for v in vector))
    if length==0: raise ValueError('Empty embedding vector.')
    return [v/length for v in vector]


def embed(settings, text, query=False):
    # Bound by UTF-8 bytes, not characters, for multilingual context safety.
    encoded=text.encode('utf-8')
    chunks=[]
    while encoded:
        end=min(1200,len(encoded))
        while True:
            try: chunk=encoded[:end].decode('utf-8');break
            except UnicodeDecodeError: end-=1
        chunks.append(chunk);encoded=encoded[end:]
    chunks=chunks or ['']
    vectors=[]
    with httpx.Client(timeout=settings.embedding_timeout_seconds) as client:
        for chunk in chunks:
            response=client.post(settings.ollama_base_url+'/api/embed',json={'model':settings.embedding_model,'input':('search_query: ' if query else 'search_document: ')+chunk,'truncate':False,'keep_alive':'2m'})
            response.raise_for_status()
            batch=response.json()['embeddings']
            if len(batch)!=1: raise ValueError('Embedding response has wrong item count.')
            vector=unit(batch[0])
            if vectors and len(vector)!=len(vectors[0]): raise ValueError('Embedding dimensions changed.')
            vectors.append(vector)
    return unit([sum(v[i] for v in vectors)/len(vectors) for i in range(len(vectors[0]))])


def create_index(settings):
    with connect_database(settings.database_path) as db:
        db.execute("INSERT OR REPLACE INTO app_metadata VALUES ('search_enabled','1')")
    return work_jobs.create(settings,'index')


def run_index(settings,job_id):
    with INDEX_LOCK:
        try:
            job=work_jobs.get(settings,job_id)
            if job['status']=='cancel_requested':
                work_jobs.update(settings,job_id,status='cancelled',stage='cancelled',completed_at=work_jobs.now());return
            if job['status'] not in ('queued','processing'): return
            work_jobs.update(settings,job_id,status='processing',stage='discovering changed content',started_at=job['started_at'] or work_jobs.now())
            with connect_database(settings.database_path) as db:
                docs=documents(db)
                revision=db.execute("SELECT value FROM app_metadata WHERE key='search_revision'").fetchone()[0]
                existing={(r['entity_type'],r['entity_id']):dict(r) for r in db.execute('SELECT * FROM search_vectors')}
                db.execute('DELETE FROM work_items WHERE job_id=?',(job_id,))
            counts={'processed':0,'completed':0,'failed':0,'skipped':0}
            work_jobs.update(settings,job_id,total=len(docs),**counts)
            for doc in docs:
                if work_jobs.cancelled(settings,job_id):
                    work_jobs.update(settings,job_id,status='cancelled',stage='cancelled',completed_at=work_jobs.now());return
                work_jobs.update(settings,job_id,current_item=f"{doc['kind']}: {doc['title']}",stage='generating local embeddings')
                status='skipped';message=None
                cached=existing.get((doc['kind'],doc['id']))
                try:
                    if not cached or cached['fingerprint']!=doc['fingerprint'] or cached['model']!=settings.embedding_model:
                        with MODEL_GATE: vector=embed(settings,doc['text'])
                        with connect_database(settings.database_path) as db:
                            # Re-read after model work; a deletion/edit must not resurrect stale content.
                            current=next(iter(documents(db,doc['kind'],doc['id'])),None)
                            if current and current['fingerprint']==doc['fingerprint']:
                                db.execute('INSERT OR REPLACE INTO search_vectors VALUES (?,?,?,?,?,?,?)',(doc['kind'],doc['id'],doc['fingerprint'],settings.embedding_model,len(vector),struct.pack('<'+'f'*len(vector),*vector),work_jobs.now()))
                                status='completed'
                            else: message='Content changed during indexing; queued for the next incremental pass.'
                except Exception as error:
                    status='failed';message=str(error)[:1000]
                counts[status]+=1;counts['processed']+=1
                with connect_database(settings.database_path) as db:
                    db.execute('INSERT OR REPLACE INTO work_items VALUES (?,?,?,?,?)',(job_id,doc['kind'],doc['id'],status,message))
                work_jobs.update(settings,job_id,**counts)
            with connect_database(settings.database_path) as db:
                db.execute("INSERT OR REPLACE INTO app_metadata VALUES ('search_observed_revision',?)",(revision,))
            work_jobs.update(settings,job_id,status='completed_with_errors' if counts['failed'] else 'completed',stage='finished',completed_at=work_jobs.now())
        except Exception as error:
            work_jobs.update(settings,job_id,status='failed',stage='failed',error=str(error)[:1000],completed_at=work_jobs.now())


def maintain(settings,stop):
    while not stop.wait(2):
        with connect_database(settings.database_path) as db:
            values=dict(db.execute("SELECT key,value FROM app_metadata WHERE key LIKE 'search_%'"))
        if values.get('search_enabled')=='1' and values.get('search_revision')!=values.get('search_observed_revision'):
            job=work_jobs.create(settings,'index')
            run_index(settings,job['job_id'])


def retrieve(settings,q,mode='hybrid',kind=None,tag_ids=(),organisation_id=None,year=None,deck_id=None,offset=0,limit=50):
    with connect_database(settings.database_path) as db:
        docs=documents(db)
        vectors={(r['entity_type'],r['entity_id']):dict(r) for r in db.execute('SELECT * FROM search_vectors WHERE model=?',(settings.embedding_model,))}
        categories={r['id']:r['tag_type'] for r in db.execute('SELECT id,tag_type FROM tags WHERE status=\'active\'')}
    groups={}
    for value in tag_ids: groups.setdefault(categories.get(value,'unknown'),set()).add(value)
    docs=[d for d in docs if (not kind or d['kind']==kind) and (not organisation_id or d['organisation_id']==organisation_id) and (not year or d['year']==year) and (not deck_id or d['deck_id']==deck_id) and all(values & {t['id'] for t in d['tags']+d['deck_tags'] if t['status']=='active'} for values in groups.values())]
    words=re.findall(r'\w+',q.casefold()); literal=q.strip().casefold()
    keyword=sorted([(d,(10 if literal and literal in d['text'].casefold() else 0)+sum(d['text'].casefold().count(w) for w in words)) for d in docs],key=lambda pair:(-pair[1],pair[0]['id']))
    keyword=[d for d,score in keyword if score>0 or not literal]
    semantic=[];warning=None
    if mode!='keyword' and literal:
        try:
            if not MODEL_GATE.acquire(timeout=.1):
                raise ValueError('Local model is busy; retry when enrichment/indexing finishes')
            try: query=embed(settings,q,True)
            finally: MODEL_GATE.release()
            for d in docs:
                cached=vectors.get((d['kind'],d['id']))
                if cached and cached['fingerprint']==d['fingerprint'] and cached['dimension']==len(query):
                    values=struct.unpack('<'+'f'*len(query),cached['vector'])
                    similarity=sum(a*b for a,b in zip(query,values))
                    if similarity>0: semantic.append((d,similarity))
            semantic.sort(key=lambda item:(-item[1],item[0]['id']))
        except Exception as error:
            warning=f'Local semantic search unavailable: {error}. Keyword results remain available.'
            if mode=='semantic': raise HTTPException(503,warning) from error
    ranking={};by_id={}
    lists=[keyword] if mode=='keyword' else [[d for d,_ in semantic]] if mode=='semantic' else [keyword,[d for d,_ in semantic]]
    for results in lists:
        for rank,d in enumerate(results,1):
            key=(d['kind'],d['id']);ranking[key]=ranking.get(key,0)+1/(60+rank);by_id[key]=d
    ordered=sorted(ranking,key=lambda key:(-ranking[key],key))
    items=[]
    for key in ordered[offset:offset+limit]:
        d=by_id[key].copy();text=d.pop('text');d.pop('fingerprint');d.pop('deck_tags')
        position=next((text.casefold().find(w) for w in words if w in text.casefold()),0)
        d['matching_text']=text[max(0,position-60):position+300];d['rank_score']=ranking[key]
        items.append(d)
    stale=sum(1 for d in docs if (d['kind'],d['id']) not in vectors or vectors[d['kind'],d['id']]['fingerprint']!=d['fingerprint'])
    return {'items':items,'total':len(ordered),'offset':offset,'limit':limit,'warning':warning,'pending_index_count':stale,'ranking':'Reciprocal rank fusion (k=60); scores are not percentages.'}
