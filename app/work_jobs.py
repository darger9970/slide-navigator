"""Durable progress shared by indexing and native exports."""
import json
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from app.database import connect_database


def now():
    return datetime.now(timezone.utc).isoformat()


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS work_jobs (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
        stage TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
        total INTEGER, processed INTEGER NOT NULL DEFAULT 0,
        completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0, current_item TEXT,
        created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
        error TEXT, output_path TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS work_items (
        job_id TEXT NOT NULL REFERENCES work_jobs(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, status TEXT NOT NULL,
        message TEXT, PRIMARY KEY(job_id,entity_type,entity_id))""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_index_job_active ON work_jobs(kind) WHERE kind='index' AND status IN ('queued','processing','cancel_requested')")


def create(settings, kind, options=None):
    with connect_database(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if kind == 'index':
            active = db.execute("SELECT id FROM work_jobs WHERE kind='index' AND status IN ('queued','processing','cancel_requested')").fetchone()
            if active:
                return {'job_id': active[0], 'reused': True}
        job_id = str(options['request_id']) if kind=='export' and options else str(uuid.uuid4())
        existing=db.execute('SELECT kind,options FROM work_jobs WHERE id=?',(job_id,)).fetchone()
        if existing:
            if existing['kind']!=kind or json.loads(existing['options'])!=(options or {}):
                raise HTTPException(409,'This request ID was already used for another export.')
            return {'job_id':job_id,'reused':True}
        db.execute("INSERT INTO work_jobs(id,kind,status,stage,options,created_at) VALUES (?,?,'queued','queued',?,?)", (job_id,kind,json.dumps(options or {}),now()))
    return {'job_id':job_id,'reused':False}


def update(settings, job_id, **values):
    allowed={'status','stage','total','processed','completed','failed','skipped','current_item','started_at','completed_at','error','output_path'}
    if not values or not set(values) <= allowed:
        raise ValueError('Invalid progress fields')
    with connect_database(settings.database_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if values.get('status') in ('completed','completed_with_errors'):
            row=db.execute('SELECT status FROM work_jobs WHERE id=?',(job_id,)).fetchone()
            if row and row[0]=='cancel_requested':
                values.update(status='cancelled',stage='cancelled')
        db.execute('UPDATE work_jobs SET '+','.join(f'{key}=?' for key in values)+' WHERE id=?', (*values.values(),job_id))


def cancelled(settings, job_id):
    with connect_database(settings.database_path) as db:
        return db.execute('SELECT status FROM work_jobs WHERE id=?',(job_id,)).fetchone()[0] == 'cancel_requested'


def claim(settings,job_id):
    with connect_database(settings.database_path) as db:
        return db.execute("UPDATE work_jobs SET status='processing',started_at=COALESCE(started_at,?) WHERE id=? AND status='queued'",(now(),job_id)).rowcount == 1


def get(settings, job_id):
    with connect_database(settings.database_path) as db:
        row=db.execute('SELECT * FROM work_jobs WHERE id=?',(job_id,)).fetchone()
        if not row:
            raise HTTPException(404,'Background job not found.')
        result=dict(row)
        result['items']=[dict(r) for r in db.execute('SELECT * FROM work_items WHERE job_id=? ORDER BY entity_type,entity_id',(job_id,))]
    start=datetime.fromisoformat(result['started_at'] or result['created_at'])
    end=datetime.fromisoformat(result['completed_at']) if result['completed_at'] else datetime.now(timezone.utc)
    result['elapsed_seconds']=max(0,(end-start).total_seconds())
    result['progress_percent']=100*result['processed']/result['total'] if result['total'] else None
    return result


def recover(settings):
    with connect_database(settings.database_path) as db:
        db.execute("UPDATE work_jobs SET status='cancelled',stage='cancelled',completed_at=? WHERE status='cancel_requested'",(now(),))
        db.execute("UPDATE work_jobs SET status='queued',stage='recovering' WHERE status='processing'")
        return [dict(r) for r in db.execute("SELECT id,kind FROM work_jobs WHERE status='queued'")]
