"""Read-only real-library browser checks against the running local app."""
import json
import io
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_library_browser import BrowserPage
from websockets.sync.client import connect
from PIL import Image


def main():
    with urlopen('http://127.0.0.1:8000/api/slides') as response:slides=json.load(response)['items']
    actual_previews=0
    for slide in slides:
        with urlopen(f"http://127.0.0.1:8000/api/slides/{slide['id']}/thumbnail") as response:
            assert response.headers.get_content_type()=='image/png','A placeholder remains in the live library'
            picture=Image.open(io.BytesIO(response.read()));picture.load();assert picture.width>0 and picture.height>0;actual_previews+=1
    executable=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    if not executable.is_file():executable=Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    with tempfile.TemporaryDirectory(prefix='slide-runtime-browser-') as temporary:
        profile=Path(temporary)
        process=subprocess.Popen([str(executable),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+15
            while not (profile/'DevToolsActivePort').exists() and time.monotonic()<deadline:time.sleep(.1)
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urlopen(f'http://127.0.0.1:{port}/json/list') as response:target=next(item for item in json.load(response) if item['type']=='page')
            with connect(target['webSocketDebuggerUrl'],max_size=5_000_000) as websocket:
                page=BrowserPage(websocket);page.command('Runtime.enable');page.command('Page.enable');page.command('Page.navigate',{'url':'http://127.0.0.1:8000'})
                page.wait('document.querySelectorAll("#deck-table-body tr").length===1')
                page.click('#deck-view-toggle');assert page.evaluate('!document.getElementById("deck-grid").classList.contains("hidden")')
                page.click('#deck-view-toggle')
                page.click('[data-page="tags"]');page.wait('document.querySelectorAll("#taxonomy-items article").length===2')
                page.click('[data-page="slides"]');page.wait('document.querySelectorAll("#slide-grid .slide-card").length===17')
                page.click('#slide-table-view');assert page.evaluate('!document.getElementById("slide-table-wrap").classList.contains("hidden")')
                page.click('#slide-grid-view')
                page.click('[data-page="search"]');page.change('search-mode','keyword');page.wait('document.querySelectorAll("#search-results [data-select-slide]").length===17')
                page.click('#search-results [data-select-slide]');page.command('Page.reload');page.wait('document.readyState==="complete" && selectedSlides.size===1 && document.querySelector(".native-builder") !== null')
                page.click('#create-deck');page.click('#confirm-slide-order');assert page.evaluate('!document.getElementById("page-builder").classList.contains("hidden")')
                page.evaluate('true');assert not page.errors,page.errors
            report={'status':'passed','real_library_decks':1,'real_library_slides':17,'actual_png_previews':actual_previews,'checks':['deck grid/table','slide grid/table','Tag Manager','exact search','search selection refresh persistence','ordered builder review'],'runtime_exceptions':0,'database_edits':False}
            Path('docs/PROGRAMME_RUNTIME_BROWSER.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
        finally:
            if (profile/'DevToolsActivePort').exists():
                port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
                try:
                    with urlopen(f'http://127.0.0.1:{port}/json/version') as response:address=json.load(response)['webSocketDebuggerUrl']
                    with connect(address) as websocket:websocket.send(json.dumps({'id':1,'method':'Browser.close'}))
                except OSError:pass
            if process.poll() is None:process.terminate()
            process.wait(timeout=10);time.sleep(.5)


if __name__=='__main__':main()
