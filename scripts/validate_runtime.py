"""Read-only library checks across starting/restarting the installed workspace app."""
import hashlib
import json
import os
import sqlite3
import subprocess
import socket
import sys
import time
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import load_settings


def snapshot():
    settings=load_settings()
    with sqlite3.connect(settings.database_path) as db:
        tables={}
        for name in ['decks','slides','tags','deck_tags','slide_tags','tag_aliases','edit_history']:
            rows=db.execute(f'SELECT * FROM {name} ORDER BY 1,2').fetchall()
            tables[name]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
        files=[hashlib.sha256(Path(row[0]).read_bytes()).hexdigest() for row in db.execute('SELECT stored_path FROM decks ORDER BY id')]
        schema=db.execute('SELECT value FROM app_metadata WHERE key=?',('schema_version',)).fetchone()[0]
        integrity=db.execute('PRAGMA foreign_key_check').fetchall()
    return {'tables':tables,'source_hashes':files,'schema':schema,'foreign_key_errors':integrity}


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',8000))
    report_path=Path('docs/PROGRAMME_RUNTIME_VALIDATION.json')
    report=json.loads(report_path.read_text()) if report_path.exists() else {'before':snapshot(),'checks':[]}
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    log=Path('.runtime-programme.log').open('a',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:
            with urlopen('http://127.0.0.1:8000/health',timeout=2) as response:
                if json.load(response)['status']=='ok':break
        except OSError:time.sleep(.1)
    else:raise RuntimeError('Application did not start; inspect .runtime-programme.log')
    after=snapshot()
    assert after['tables']==report['before']['tables'],'Existing library rows changed'
    assert after['source_hashes']==report['before']['source_hashes'],'A source PowerPoint changed'
    assert after['foreign_key_errors']==[]
    for route in ['/api/decks','/api/slides','/api/filters','/api/taxonomy','/api/search?mode=keyword&q=','/api/work-jobs']:
        with urlopen('http://127.0.0.1:8000'+route,timeout=5) as response:assert response.status==200
    report['checks'].append({'status':'passed','schema':after['schema'],'tables_unchanged':True,'source_files_unchanged':True,'foreign_key_errors':0,'pid':process.pid})
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report['checks'][-1]))


if __name__=='__main__':main()
