"""Real headless-browser acceptance tests; no frontend toolchain is required.

Uses Edge/Chrome and websockets (already included by uvicorn[standard]).
Skips explicitly when no compatible local browser is installed.
"""

import json
import base64
import os
from contextlib import ExitStack
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from urllib.request import urlopen

import pytest
import uvicorn

from app.main import create_app
from test_library import library  # noqa: F401 -- shared persisted-data fixture
from test_enrichment import ollama  # noqa: F401 -- mocked local model transport


class BrowserPage:
    def __init__(self, websocket):
        self.websocket = websocket
        self.sequence = 0
        self.errors = []

    def command(self, method, params=None):
        self.sequence += 1
        sequence = self.sequence
        self.websocket.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.websocket.recv(timeout=10))
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(message["params"])
            if message.get("id") == sequence:
                assert "error" not in message, message
                return message.get("result", {})

    def evaluate(self, expression):
        result = self.command("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        assert "exceptionDetails" not in result, result
        return result["result"].get("value")

    def wait(self, expression):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.05)
        pytest.fail(f"Browser condition timed out: {expression}; errors: {self.errors}")

    def click(self, selector):
        self.evaluate(f"document.querySelector({json.dumps(selector)}).click()")

    def change(self, element_id, value):
        self.evaluate(f"""(() => {{ const input = document.getElementById({json.dumps(element_id)});
            input.value = {json.dumps(value)}; input.dispatchEvent(new Event('change', {{bubbles:true}})); }})()""")


@pytest.fixture(scope="module")
def browser_process(tmp_path_factory):
    websockets = pytest.importorskip("websockets.sync.client")
    candidates = [os.environ.get("SLIDE_NAVIGATOR_TEST_BROWSER"), shutil.which("chromium"), shutil.which("google-chrome"),
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    executable = next((path for path in candidates if path and Path(path).is_file()), None)
    if not executable:
        pytest.skip("Edge/Chrome is not installed; browser acceptance remains unverified")
    profile = tmp_path_factory.mktemp("library-browser")
    process = subprocess.Popen([
        executable, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
        "--remote-debugging-port=0", "--remote-allow-origins=*", f"--user-data-dir={profile}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 15
        active_port = profile / "DevToolsActivePort"
        while not active_port.exists() and time.monotonic() < deadline:
            # Edge's launcher may exit successfully after handing off to a child.
            time.sleep(0.1)
        assert active_port.exists(), "Browser DevTools did not start"
        port = active_port.read_text().splitlines()[0]
        yield f"http://127.0.0.1:{port}", websockets
    finally:
        if (profile / "DevToolsActivePort").exists():
            port = (profile / "DevToolsActivePort").read_text().splitlines()[0]
            try:
                with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as response:
                    address = json.load(response)["webSocketDebuggerUrl"]
                with websockets.connect(address) as connection:
                    connection.send(json.dumps({"id": 1, "method": "Browser.close"}))
            except (OSError, TimeoutError):
                pass
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def page(browser_process, library):
    _, settings, _, _ = library
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    port = server_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [server_socket]}, daemon=True)
    thread.start()
    endpoint, websocket_client = browser_process
    websocket = None
    browser = None
    target_id = None
    connections = ExitStack()
    try:
        deadline = time.monotonic() + 8
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        with urlopen(f"{endpoint}/json/version") as response:
            address = json.load(response)["webSocketDebuggerUrl"]
        browser = BrowserPage(connections.enter_context(websocket_client.connect(address, max_size=5_000_000)))
        target_id = browser.command("Target.createTarget", {"url": "about:blank"})["targetId"]
        with urlopen(f"{endpoint}/json/list") as response:
            target = next(item for item in json.load(response) if item["id"] == target_id)
        websocket = connections.enter_context(websocket_client.connect(target["webSocketDebuggerUrl"], max_size=5_000_000))
        result = BrowserPage(websocket)
        result.command("Runtime.enable")
        result.command("Page.enable")
        result.command("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        result.command("Page.navigate", {"url": f"http://127.0.0.1:{port}"})
        result.wait("document.querySelectorAll('#deck-table-body tr').length === 3 && document.querySelectorAll('#slide-tag-filters input').length > 0")
        yield result
        result.evaluate("true")  # Drain any outstanding runtime exception events.
        assert not result.errors, result.errors
    finally:
        if browser:
            if target_id:
                browser.command("Target.closeTarget", {"targetId": target_id})
        connections.close()
        server.should_exit = True
        thread.join(timeout=10)
        server_socket.close()


def open_slides(page):
    page.click('[data-page="slides"]')
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")


def test_deck_description_wraps_without_clipping_and_views_work(page, library):
    client, _, decks, _ = library
    deck_id = decks["alpha"]
    short_height = page.evaluate("document.querySelector('#deck-table-body tr').getBoundingClientRect().height")
    description = ("A detailed presentation description that should wrap naturally. " * 30
                   + "\n" + "UnbrokenReference" * 100)
    assert client.patch(f"/api/decks/{deck_id}", json={"description": description}).status_code == 200
    page.click("#refresh-decks")
    page.wait("[...document.querySelectorAll('.deck-description-preview')].some(cell => cell.textContent.includes('UnbrokenReference'))")
    for width in [1280, 650, 320]:
        page.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        geometry = page.evaluate("""(() => {
            const cell = [...document.querySelectorAll('.deck-description-preview')].find(cell => cell.textContent.includes('UnbrokenReference'));
            const range = document.createRange(); range.selectNodeContents(cell);
            return {text: cell.textContent, lines: range.getClientRects().length,
                height: cell.closest('tr').getBoundingClientRect().height,
                clipped: cell.scrollHeight > cell.clientHeight + 1,
                overflow: cell.scrollWidth > cell.clientWidth + 1,
                pageOverflow: document.documentElement.scrollWidth > window.innerWidth};
        })()""")
        assert geometry["text"] == description
        assert geometry["lines"] > 2 and geometry["height"] > short_height
        assert not geometry["clipped"] and not geometry["overflow"] and not geometry["pageOverflow"]
    page.click("#deck-view-toggle")
    assert page.evaluate("document.getElementById('deck-grid').checkVisibility()")
    assert page.evaluate("document.querySelectorAll('#deck-grid .deck-card').length") == 3
    page.click("#deck-view-toggle")
    assert page.evaluate("document.getElementById('deck-table-body').checkVisibility()")


def test_thumbnail_retry_refreshes_deck_and_slide_images_without_losing_edits(page, library, monkeypatch):
    import pymupdf
    from dataclasses import replace
    from app import thumbnails
    from app.ingestion import _set_rendering
    from app.database import connect_database
    _, _, decks, _ = library
    deck_id = decks["alpha"]
    original_create = thumbnails.create_job
    monkeypatch.setattr(thumbnails, "libreoffice_available", lambda: True)
    monkeypatch.setattr(thumbnails, "create_job", lambda settings, id: original_create(replace(settings, rendering_enabled=True), id))
    def rendered(settings, job_id):
        job = thumbnails.get_job(settings, job_id)
        with connect_database(settings.database_path) as db:
            slides = db.execute("SELECT id, slide_number FROM slides WHERE deck_id = ?", (job["deck_id"],)).fetchall()
        directory = settings.thumbnails_dir / job["deck_id"]; directory.mkdir(parents=True, exist_ok=True)
        for slide in slides:
            path = directory / f"slide-{slide['slide_number']}.png"
            pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 640, 360), False)
            pixels.clear_with(200); pixels.save(path)
            _set_rendering(settings, slide["id"], "available", thumbnail_path=path.relative_to(settings.thumbnails_dir).as_posix())
        with connect_database(settings.database_path) as db:
            db.execute("UPDATE thumbnail_jobs SET status = 'completed', stage = 'completed', processed = total WHERE id = ?", (job_id,))
    monkeypatch.setattr(thumbnails, "run_job", rendered)
    cover = f"document.querySelector('#deck-table-body input[data-select-deck=\"{deck_id}\"]').parentElement.querySelector('img')"
    previous = page.evaluate(cover + ".src")
    page.evaluate(f"openDeckDetails({json.dumps(deck_id)})")
    page.wait("document.getElementById('deck-dialog').open")
    page.change("deck-edit-description", "Keep unsaved text")
    page.click("#regenerate-thumbnails")
    page.wait("document.getElementById('thumbnail-retry-status').textContent === 'Thumbnails ready.'")
    page.wait(cover + f".src !== {json.dumps(previous)}")
    assert page.evaluate("document.getElementById('deck-edit-description').value") == "Keep unsaved text"
    assert page.evaluate(f"fetch(({cover}).src).then(response => response.headers.get('content-type'))").startswith("image/png")
    page.click("#deck-dialog-close")
    open_slides(page)
    assert page.evaluate("[...document.querySelectorAll('#slide-grid .slide-card')].filter(card => card.textContent.includes('Thumbnail ready')).length") == 2


def test_library_toolbar_control_geometry_and_filtering(page):
    for kind in ["decks", "slides"]:
        page.click(f'[data-page="{kind}"]')
        if kind == "slides":
            page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")
        for width in [1280, 820, 650, 320]:
            page.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
            controls = page.evaluate(f"""[...document.querySelectorAll('#page-{kind} .library-toolbar .library-search, #page-{kind} .library-toolbar select, #page-{kind} .library-toolbar > button, #page-{kind} .library-toolbar .view-toggle')].map(node => {{
                const rect = node.getBoundingClientRect(); const style = getComputedStyle(node);
                return {{top: rect.top, bottom: rect.bottom, height: rect.height, left: rect.left,
                    right: rect.right, paddingLeft: style.paddingLeft, select: node.tagName === 'SELECT'}};
            }})""")
            expected_height = 36 if width <= 650 else 39
            assert all(abs(control["height"] - expected_height) < 1 for control in controls), (kind, width, controls)
            assert all(control["paddingLeft"] == "10px" for control in controls if control["select"])
            for left in controls:
                assert left["left"] >= 0 and left["right"] <= width + 1
                for right in controls:
                    if min(left["bottom"], right["bottom"]) > max(left["top"], right["top"]) + 1:
                        assert abs(left["top"] - right["top"]) < 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("document.getElementById('slide-search').value = 'no-match'; document.getElementById('slide-search').dispatchEvent(new Event('input'))")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 0")
    page.click("#clear-slide-filters")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")
    page.click("#slide-table-view")
    assert page.evaluate("document.getElementById('slide-table-wrap').checkVisibility()")
    page.click("#slide-grid-view")
    assert page.evaluate("document.getElementById('slide-grid').checkVisibility()")


def test_ai_generate_refresh_review_edit_reject_and_regenerate(page, library, ollama):
    client, _, decks, _ = library
    deck_id = decks["untagged"]
    page.click(f'#deck-table-body [data-select-deck="{deck_id}"]')
    page.click("#generate-selected-ai")
    page.wait("document.getElementById('ai-model').options.length === 2")
    page.click('#ai-config-form button[type=submit]')
    page.wait("document.getElementById('ai-job-progress')?.textContent.includes(': completed ·')")
    page.command("Page.reload", {"ignoreCache": True})
    page.wait("document.getElementById('ai-job-progress')?.textContent.includes(': completed ·')")
    page.evaluate(f"openDeckDetails({json.dumps(deck_id)})")
    page.wait("document.querySelector('#deck-ai-suggestions [data-ai-suggestion]') !== null")
    page.click("#deck-ai-review-later")
    assert page.evaluate("document.getElementById('deck-ai-suggestions').hidden")
    assert page.evaluate("document.getElementById('deck-ai-review-later').textContent") == "Show suggestions"
    page.click("#deck-ai-review-later")
    assert client.get(f"/api/decks/{deck_id}").json()["name"] == "Untagged"
    page.evaluate("document.querySelector('#deck-ai-suggestions article input').value = 'Human edited AI suggestion'")
    page.click("#deck-ai-suggestions article button")  # Accept edited suggestion.
    page.wait("document.getElementById('deck-edit-title').value === 'Human edited AI suggestion'")
    assert client.get(f"/api/decks/{deck_id}").json()["metadata_provenance"]["title"]["source"] == "manual"
    # Regenerate is explicit and preserves the approved title.
    page.wait("document.querySelector('#deck-ai-suggestions article button')?.textContent === 'Regenerate'")
    page.click("#deck-ai-suggestions article button")
    page.wait("document.getElementById('ai-config-dialog').open")
    assert page.evaluate("document.getElementById('ai-force').checked")
    page.wait("!document.querySelector('#ai-config-form button[type=submit]').disabled")
    page.click('#ai-config-form button[type=submit]')
    page.wait("document.querySelectorAll('#deck-ai-suggestions article').length === 2")
    page.evaluate("[...document.querySelectorAll('#deck-ai-suggestions article:first-child button')].find(b => b.textContent === 'Reject').click()")
    page.wait("document.querySelector('#deck-ai-suggestions article strong')?.textContent.includes('rejected')")
    assert client.get(f"/api/decks/{deck_id}").json()["name"] == "Human edited AI suggestion"
    page.click("#deck-dialog-close")
    for width in [320, 650, 1280]:
        page.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_running_ai_job_survives_browser_refresh(page, library, ollama, monkeypatch):
    from app import enrichment
    client, _, decks, _ = library
    original = enrichment.generate
    reached, release = threading.Event(), threading.Event()
    def pause(*args, **kwargs):
        reached.set()
        assert release.wait(timeout=20)
        return original(*args, **kwargs)
    monkeypatch.setattr(enrichment, "generate", pause)
    try:
        page.click(f'#deck-table-body [data-select-deck="{decks["alpha"]}"]')
        page.click("#generate-selected-ai")
        page.wait("!document.querySelector('#ai-config-form button[type=submit]').disabled")
        page.click('#ai-config-form button[type=submit]')
        assert reached.wait(timeout=5)
        page.wait("document.getElementById('ai-job-current').textContent.includes('alpha.pptx')")
        page.command("Page.reload", {"ignoreCache": True})
        page.wait("document.getElementById('ai-job-current')?.textContent.includes('alpha.pptx')")
        assert len(client.get("/api/enrichment/jobs").json()["items"]) == 1
        release.set()
        page.wait("document.getElementById('ai-job-progress').textContent.includes(': completed ·')")
    finally:
        release.set()


def test_selected_slide_ai_and_metadata_state_labels(page, library, ollama):
    client, _, _, slides = library
    slide_id = slides["untagged"][0]["id"]
    open_slides(page)
    page.click(f'#slide-grid [data-select-slide="{slide_id}"]')
    page.click("#generate-selected-slides-ai")
    page.wait("!document.querySelector('#ai-config-form button[type=submit]').disabled")
    page.click('#ai-config-form button[type=submit]')
    page.wait("document.getElementById('ai-job-progress').textContent.includes(': completed ·')")
    job = client.get(f"/api/enrichment/jobs/{page.evaluate('aiJobId')}").json()
    assert job["deck_progress"]["total"] == 0 and job["slide_progress"]["total"] == 1
    assert job["items"][0]["result_id"], job["items"]
    assert client.post("/api/enrichment/review", json={"result_ids": [job["items"][0]["result_id"]], "action": "accept"}).status_code == 200
    page.evaluate(f"openSlideDetails({json.dumps(slide_id)})")
    page.wait("document.querySelector('#slide-edit-form [data-metadata-state=title]')?.textContent === 'Approved metadata'")
    assert "AI suggestions" in page.evaluate("document.getElementById('slide-ai-suggestions').textContent")
    page.evaluate("const titleField = document.getElementById('slide-edit-title'); titleField.value = 'User override'; titleField.dispatchEvent(new Event('input'))")
    assert "unsaved" in page.evaluate("document.querySelector('#slide-edit-form [data-metadata-state=title]').textContent")
    page.click('#slide-edit-form button[type=submit]')
    page.wait("document.querySelector('#slide-edit-form [data-metadata-state=title]').textContent === 'User edited / overridden'")


def test_optional_post_import_ai_queues_without_auto_acceptance(page, library, ollama):
    from test_ingestion import create_presentation
    client, _, _, _ = library
    payload = base64.b64encode(create_presentation("Optional imported title")).decode()
    page.click('[data-page="import"]')
    assert not page.evaluate("document.getElementById('import-ai-enabled').checked")
    page.click("#import-ai-enabled")
    page.wait("!document.getElementById('import-ai-model').disabled")
    page.evaluate(f"startImport([new File([Uint8Array.from(atob({json.dumps(payload)}), c => c.charCodeAt(0))], 'optional-import.pptx')])")
    page.wait("document.getElementById('ai-job-progress').textContent.includes(': completed ·')")
    assert "Optional AI suggestions queued" in page.evaluate("document.getElementById('post-import-ai-status').textContent")
    imported = client.get(f"/api/import/jobs/{page.evaluate('watchedImportJob')}").json()
    deck = client.get(f"/api/decks/{imported['items'][0]['deck_id']}").json()
    assert deck["name"] == "Optional imported title" and deck["tags"] == []
    assert deck["metadata_states"]["title"] == "existing_unreviewed"
    page.command("Page.reload", {"ignoreCache": True})
    page.wait("document.getElementById('ai-job-progress')?.textContent.includes(': completed ·')")
    assert len(client.get("/api/enrichment/jobs").json()["items"]) == 1


def test_import_refresh_reconnect_progress_and_review(page, library, monkeypatch):
    import app.ingestion as ingestion
    from datetime import datetime, timedelta, timezone
    from test_enhanced_import import metadata_presentation
    client, settings, _, _ = library
    original_progress = ingestion._job_progress
    reached = threading.Event()
    release = threading.Event()
    def pause(*args, **kwargs):
        original_progress(*args, **kwargs)
        if args[2] == "extracting_slides" and args[3] == 0:
            reached.set()
            assert release.wait(timeout=25), "Browser did not release the import worker"
    monkeypatch.setattr(ingestion, "_job_progress", pause)
    before = client.get("/api/import/jobs").json()["total"]
    payload = base64.b64encode(metadata_presentation()).decode()
    page.click('[data-page="import"]')
    try:
        page.evaluate(f"""startImport([new File([Uint8Array.from(atob({json.dumps(payload)}), c => c.charCodeAt(0))], 'Review batch 2025.pptx')])""")
        assert reached.wait(timeout=8)
        page.wait("document.getElementById('current-deck-name').textContent.includes('Review batch')")
        assert page.evaluate("document.getElementById('progress-bar').value") == 0
        page.wait("document.getElementById('slide-progress-count').textContent.includes('0 / 1')")
        with sqlite3.connect(settings.database_path) as db:
            started = (datetime.now(timezone.utc) - timedelta(seconds=3661)).isoformat()
            db.execute("UPDATE import_jobs SET started_at = ? WHERE status = 'processing'", (started,))
        page.command("Page.reload", {"ignoreCache": True})
        page.wait("document.getElementById('current-deck-name').textContent.includes('Review batch')")
        page.click('[data-page="import"]')
        page.wait("document.getElementById('import-elapsed').textContent.startsWith('01:01:')")
        page.evaluate("""window.realImportFetch = window.fetch; window.failStatusOnce = true;
            window.fetch = (...args) => {
                if (String(args[0]).includes('/api/import/jobs/') && window.failStatusOnce) {
                    window.failStatusOnce = false; return Promise.reject(new Error('test connection interruption'));
                }
                return window.realImportFetch(...args);
            };""")
        page.wait("document.getElementById('selection').textContent.includes('Retrying status')")
        release.set()
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait("document.getElementById('progress-title').textContent.includes('review your decks')")
        assert client.get("/api/import/jobs").json()["total"] == before + 1
        elapsed = page.evaluate("document.getElementById('import-elapsed').textContent")
        page.evaluate("new Promise(resolve => setTimeout(resolve, 1200))")
        assert page.evaluate("document.getElementById('import-elapsed').textContent") == elapsed
        page.click("#defer-import-review")
        assert not page.evaluate("document.getElementById('import-review-panel').open")
        page.evaluate("document.getElementById('import-review-panel').open = true")
        page.click("#progress-items [data-review-deck]")
        page.wait("document.getElementById('deck-dialog').open")
        assert page.evaluate("document.getElementById('deck-edit-year').value") == "2025"
        assert "filename" in page.evaluate("document.getElementById('deck-metadata-provenance').textContent")
        for field, value in [("title", "Reviewed deck"), ("organisation", "BT"), ("year", "2027"),
                             ("author", "Reviewer"), ("subject", "New subject"), ("subtitle", "New subtitle"), ("description", "Reviewed description")]:
            page.change(f"deck-edit-{field}", value)
        # Custom canonical tags are available directly in the review dialog.
        page.evaluate("""const add = document.querySelector('#deck-detail-tags select'); add.value = '__new__'; add.dispatchEvent(new Event('change'));""")
        page.wait("document.getElementById('new-tag-dialog').open")
        page.change("new-tag-name", "Review classification")
        page.click('#new-tag-form button[type=submit]')
        page.wait("!document.getElementById('new-tag-dialog').open")
        page.wait("document.querySelector('#deck-tag-editor input:checked') !== null")
        page.click("#mark-deck-reviewed")
        page.wait("!document.querySelector('#deck-edit-form button[type=submit]').disabled")
        assert page.evaluate("document.getElementById('deck-edit-error').textContent") == ""
        deck_id = page.evaluate("activeDeckId")
        saved = client.get(f"/api/decks/{deck_id}").json()
        assert saved["review_status"] == "Reviewed"
        assert saved["name"] == "Reviewed deck" and saved["year"] == 2027
        assert saved["author"] == "Reviewer" and saved["subtitle"] == "New subtitle"
        assert saved["tags"][0]["name"] == "Review classification"
        page.click("#deck-dialog-close")
        page.wait("document.querySelector('#progress-items [data-review-deck]').textContent.includes('Reviewed')")
    finally:
        release.set()


def test_deck_details_save_and_refresh(page, library):
    client, settings, decks, _ = library
    deck_id = decks["untagged"]
    page.evaluate(f"openDeckDetails({json.dumps(deck_id)})")
    page.wait("document.getElementById('deck-dialog').open")
    for field, value in [("title", "BT Salesforce takeout"), ("organisation", "BT"),
                         ("year", "2023"), ("description", "")]:
        page.change(f"deck-edit-{field}", value)
    page.click('#deck-tag-editor input[value="proposal"]')
    page.click('#deck-edit-form button[type="submit"]')
    page.wait("!document.querySelector('#deck-edit-form button[type=submit]').disabled")
    assert page.evaluate("document.getElementById('deck-edit-error').textContent") == ""
    expected = {"name": "BT Salesforce takeout", "organisation": "BT", "year": 2023, "description": ""}
    saved = client.get(f"/api/decks/{deck_id}").json()
    for key, value in expected.items():
        assert (saved[key] or "") == value
    assert [tag["id"] for tag in saved["tags"]] == ["proposal"]
    page.change("deck-edit-description", "Updated description")
    page.click('#deck-edit-form button[type="submit"]')
    page.wait("!document.querySelector('#deck-edit-form button[type=submit]').disabled")
    page.command("Page.reload", {"ignoreCache": True})
    page.wait("typeof openDeckDetails === 'function'")
    page.evaluate(f"openDeckDetails({json.dumps(deck_id)})")
    page.wait("document.getElementById('deck-dialog').open")
    assert page.evaluate("document.getElementById('deck-edit-description').value") == "Updated description"
    assert page.evaluate("document.querySelector('#deck-tag-editor input[value=proposal]').checked")
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings)) as restarted:
        persisted = restarted.get(f"/api/decks/{deck_id}").json()
        assert persisted["name"] == expected["name"]
        assert persisted["organisation"] == "BT"
        assert persisted["year"] == 2023
        assert persisted["description"] == "Updated description"
        assert [tag["id"] for tag in persisted["tags"]] == ["proposal"]


def test_validation_response_shows_field_and_reason(page):
    # The stale server rejected tag_ids with this Pydantic response shape.
    message = page.evaluate("""(async () => {
        const original = window.fetch;
        window.fetch = async () => new Response(JSON.stringify({detail: [{
            type: 'extra_forbidden', loc: ['body', 'tag_ids'],
            msg: 'Extra inputs are not permitted', input: []
        }]}), {status: 422, headers: {'Content-Type': 'application/json'}});
        try { await fetchJSON('/api/decks/example'); }
        catch (error) { return error.message; }
        finally { window.fetch = original; }
    })()""")
    assert message == "tag_ids: Extra inputs are not permitted"


def test_deck_selection_cancel_delete_and_empty_library(page, library):
    client, _, decks, slides = library
    for slide in [slides["alpha"][0], slides["untagged"][0]]:
        page.evaluate(f"toggleSlide({json.dumps(slide)})")
    page.evaluate("confirmedSlideOrder = selectedSlides.manifest()")
    page.click("#deck-view-toggle")
    checkbox = f'#deck-grid [data-select-deck="{decks["alpha"]}"]'
    page.evaluate(f"document.querySelector({json.dumps(checkbox)}).focus()")
    for event_type in ["keyDown", "keyUp"]:
        page.command("Input.dispatchKeyEvent", {"type": event_type, "key": " ", "code": "Space", "windowsVirtualKeyCode": 32})
    assert page.evaluate("selectedDecks.size") == 1
    page.click(checkbox)  # Deselect again before the multi-selection workflow.
    page.click(f'#deck-grid [data-select-deck="{decks["alpha"]}"]')
    page.click(f'#deck-grid [data-select-deck="{decks["beta"]}"]')
    assert page.evaluate("document.getElementById('delete-decks').textContent") == "Delete Selected (2)"
    # Selection is separate from preview and is synchronized with the table.
    assert not page.evaluate("document.getElementById('deck-dialog').open")
    page.click("#deck-view-toggle")
    assert page.evaluate("document.querySelectorAll('#deck-table-body [data-select-deck]:checked').length") == 2
    page.click("#delete-decks")
    page.wait("document.getElementById('delete-library-dialog').open")
    assert "2 decks and their 4 associated slides" in page.evaluate("document.getElementById('delete-library-summary').textContent")
    page.click("#cancel-library-delete")
    assert client.get("/api/decks").json()["total"] == 3
    page.click("#delete-decks")
    page.wait("document.getElementById('delete-library-dialog').open")
    page.click("#confirm-library-delete")
    page.wait("document.getElementById('decks-count').textContent === '1 presentation'")
    page.wait("document.getElementById('slides-count').textContent === '2 slides'")
    assert page.evaluate("selectedDecks.size") == 0
    assert page.evaluate("selectedSlides.size") == 1
    assert page.evaluate("confirmedSlideOrder === null")
    assert page.evaluate("selectedSlides.values()[0].deck_id") == decks["untagged"]
    assert client.get("/api/decks").json()["total"] == 1
    page.click('#deck-table-body [data-select-deck]')
    page.click("#delete-decks")
    page.wait("document.getElementById('delete-library-dialog').open")
    page.click("#confirm-library-delete")
    page.wait("document.getElementById('decks-count').textContent === '0 presentations'")
    page.click("#deck-view-toggle")
    assert "No decks match" in page.evaluate("document.getElementById('deck-grid').textContent")
    page.click('[data-page="slides"]')
    page.wait("document.getElementById('slides-count').textContent === '0 slides'")
    assert page.evaluate("document.getElementById('slides-empty').checkVisibility()")
    assert page.evaluate("document.getElementById('delete-slides').disabled")
    for width in [320, 650, 1280]:
        page.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_slide_delete_clears_builder_selection_and_persists(page, library):
    client, _, decks, slides = library
    open_slides(page)
    ids = [slides["alpha"][0]["id"], slides["beta"][0]["id"]]
    for slide_id in ids:
        page.click(f'#slide-grid [data-select-slide="{slide_id}"]')
    page.click("#create-deck")
    page.click("#confirm-slide-order")
    page.click("#close-selected-slides")
    page.click("#delete-slides")
    page.wait("document.getElementById('delete-library-dialog').open")
    assert "2 slides" in page.evaluate("document.getElementById('delete-library-summary').textContent")
    page.click("#confirm-library-delete")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 4")
    assert page.evaluate("selectedSlides.size") == 0
    assert page.evaluate("confirmedSlideOrder === null")
    assert page.evaluate("document.getElementById('create-deck').disabled")
    assert client.get(f"/api/decks/{decks['alpha']}").json()["slide_count"] == 1
    page.command("Page.reload", {"ignoreCache": True})
    page.wait("typeof loadSlides === 'function'")
    page.click('[data-page="slides"]')
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 4")


def test_inline_tags_create_assign_remove_and_reuse(page, library):
    client, _, decks, slides = library
    page.click("#deck-view-toggle")
    deck_id = decks["untagged"]
    editor = f'#deck-grid [data-inline-id="{deck_id}"]'
    page.evaluate(f"""(() => {{ const select = document.querySelector({json.dumps(editor + ' select')});
        select.value = 'proposal'; select.dispatchEvent(new Event('change')); }})()""")
    page.wait(f"document.querySelector({json.dumps(editor)}).textContent.includes('Proposal') && !document.querySelector({json.dumps(editor + ' select')}).disabled")
    assert [t["id"] for t in client.get(f"/api/decks/{deck_id}").json()["tags"]] == ["proposal"]
    page.click(editor + ' button[aria-label="Remove tag Proposal"]')
    page.wait(f"!document.querySelector({json.dumps(editor + ' button[aria-label="Remove tag Proposal"]')})")
    assert client.get(f"/api/decks/{deck_id}").json()["tags"] == []
    open_slides(page)
    slide_id = slides["untagged"][0]["id"]
    editor = f'#slide-grid [data-inline-id="{slide_id}"]'
    page.evaluate(f"""(() => {{ const select = document.querySelector({json.dumps(editor + ' select')});
        select.value = '__new__'; select.dispatchEvent(new Event('change')); }})()""")
    page.wait("document.getElementById('new-tag-dialog').open")
    page.change("new-tag-name", "Custom library tag")
    page.click('#new-tag-form button[type="submit"]')
    page.wait("!document.getElementById('new-tag-dialog').open")
    assert page.evaluate("selectedSlides.size") == 0
    tags = client.get(f"/api/slides/{slide_id}").json()["tags"]
    assert len(tags) == 1 and tags[0]["name"] == "Custom library tag"
    tag_id = tags[0]["id"]
    page.click(editor + ' button[aria-label="Remove tag Custom library tag"]')
    page.wait(f"!document.querySelector({json.dumps(editor + ' button[aria-label="Remove tag Custom library tag"]')})")
    assert tag_id in {tag["id"] for tag in client.get("/api/filters").json()["tags"]}
    assert client.get(f"/api/slides/{slide_id}").json()["tags"] == []


def test_thumbnail_toggle_preview_and_keyboard_selection(page):
    open_slides(page)
    page.click("#slide-grid [data-select-slide]")
    assert page.evaluate("document.getElementById('create-deck').textContent") == "Create Deck (1)"
    assert page.evaluate("getComputedStyle(document.querySelector('#slide-grid .selection-check')).display") == "flex"
    assert not page.evaluate("document.getElementById('slide-dialog').open")
    page.click("#slide-grid [data-select-slide]")
    assert page.evaluate("document.getElementById('create-deck').disabled")
    page.click("#slide-grid [data-select-slide]")
    assert page.evaluate("selectedSlides.size") == 1
    page.click("#slide-grid .text-button")
    page.wait("document.getElementById('slide-dialog').open")
    assert page.evaluate("selectedSlides.size") == 1
    page.click("#dialog-close")
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    page.evaluate("document.querySelector('#slide-grid [data-select-slide]').focus()")
    assert page.evaluate("document.activeElement.matches('[data-select-slide]')")
    page.command("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
    page.command("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
    assert page.evaluate("selectedSlides.size") == 0


def test_selection_survives_filters_views_and_sorting(page, library):
    _, _, decks, slides = library
    open_slides(page)
    slide_id = slides["alpha"][0]["id"]
    page.click(f'#slide-grid [data-select-slide="{slide_id}"]')
    page.change("slide-deck-filter", decks["beta"])
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 2")
    page.click("#slide-grid [data-select-slide]")
    assert page.evaluate("selectedSlides.size") == 2
    page.click("#slide-table-view")
    page.change("slide-sort", "title")
    page.wait("!document.getElementById('slide-grid').hasAttribute('aria-busy')")
    page.click("#clear-slide-filters")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")
    assert page.evaluate("selectedSlides.size") == 2
    assert page.evaluate("document.querySelectorAll('#slide-table-body [aria-pressed=true]').length") == 2
    page.click("#slide-grid-view")
    assert page.evaluate("document.querySelectorAll('#slide-grid .is-selected[data-select-slide]').length") == 2
    page.click("#clear-selection")
    assert page.evaluate("document.getElementById('create-deck').disabled")


def test_sidebar_tag_combinations_counts_and_clear(page):
    page.click("#deck-tag-filters input")  # Proposal
    page.wait("document.querySelectorAll('#deck-table-body tr').length === 1")
    assert page.evaluate("document.getElementById('deck-tag-filters').textContent.includes('Proposal (1)')")
    page.click("#clear-deck-filters")
    page.wait("document.querySelectorAll('#deck-table-body tr').length === 3")
    open_slides(page)
    page.click("#slide-tag-filters input")  # Agenda
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 2")
    page.evaluate("[...document.querySelectorAll('#slide-tag-filters label')].find(label => label.textContent.includes('Cloud')).querySelector('input').click()")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 1")
    page.click("#slide-table-view")
    assert page.evaluate("document.querySelectorAll('#slide-tag-filters input:checked').length") == 2
    assert page.evaluate("document.getElementById('slide-tag-counts').textContent.includes('Agenda (2)')")
    page.click("#clear-slide-filters")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")


def test_review_order_removal_confirmation_and_empty_state(page):
    open_slides(page)
    for index in range(3):
        page.click(f"#slide-grid .slide-card:nth-child({index + 1}) [data-select-slide]")
    original = page.evaluate("selectedSlides.values().map(slide => slide.id)")
    page.click("#create-deck")
    page.wait("document.getElementById('selected-slides-dialog').open")
    page.click('#selected-slides-list li:nth-child(2) [data-action="up"]')
    assert page.evaluate("selectedSlides.values().map(slide => slide.id)") == [original[1], original[0], original[2]]
    assert page.evaluate("document.activeElement.closest('li').dataset.reviewSlide") == original[1]
    page.click('#selected-slides-list li:nth-child(2) [data-action="remove"]')
    page.click("#confirm-slide-order")
    assert page.evaluate("confirmedSlideOrder.slides.map(slide => slide.slide_id)") == [original[1], original[2]]
    assert "No PowerPoint file has been created" in page.evaluate("document.getElementById('review-status').textContent")
    page.click("#close-selected-slides")
    page.click("#create-deck")
    assert page.evaluate("[...document.querySelectorAll('#selected-slides-list li')].map(row => row.dataset.reviewSlide)") == [original[1], original[2]]
    page.click('#selected-slides-list li [data-action="remove"]')
    assert page.evaluate("confirmedSlideOrder === null")
    page.click('#selected-slides-list li [data-action="remove"]')
    assert page.evaluate("document.getElementById('confirm-slide-order').disabled")
    assert "No slides selected" in page.evaluate("document.getElementById('selected-slides-list').textContent")


def test_drag_drop_order_and_invalid_selection_operations(page):
    open_slides(page)
    for index in range(3):
        page.click(f"#slide-grid .slide-card:nth-child({index + 1}) [data-select-slide]")
    original = page.evaluate("selectedSlides.values().map(slide => slide.id)")
    page.click("#create-deck")
    page.evaluate("""(() => {
        const rows = document.querySelectorAll('#selected-slides-list li');
        const transfer = new DataTransfer();
        rows[0].dispatchEvent(new DragEvent('dragstart', {dataTransfer: transfer, bubbles:true}));
        rows[2].dispatchEvent(new DragEvent('drop', {dataTransfer: transfer, bubbles:true, cancelable:true}));
    })()""")
    assert page.evaluate("selectedSlides.values().map(slide => slide.id)") == [original[1], original[2], original[0]]
    assert page.evaluate("selectedSlides.move('missing', 0)") is False
    assert page.evaluate(f"selectedSlides.move({json.dumps(original[0])}, -1)") is False
    assert page.evaluate("(() => { try { new SlideSelection().manifest(); return false; } catch { return true; } })()")
    assert page.evaluate("(() => { try { new SlideSelection().toggle({id:''}); return false; } catch { return true; } })()")


def test_responsive_layout_and_no_results(page):
    open_slides(page)
    page.evaluate("document.getElementById('slide-search').value = 'no-match-anywhere'; document.getElementById('slide-search').dispatchEvent(new Event('input'))")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 0")
    assert page.evaluate("document.getElementById('slides-empty').checkVisibility()")
    assert page.evaluate("document.getElementById('create-deck').disabled")
    page.click("#clear-slide-filters")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 6")
    for width in [320, 650, 1280]:
        page.command("Emulation.setDeviceMetricsOverride", {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Horizontal overflow at {width}px"
        assert page.evaluate("document.getElementById('create-deck').getBoundingClientRect().right <= window.innerWidth")


def test_selection_persists_across_pagination_and_table_toggle(page, library):
    _, settings, decks, _ = library
    with sqlite3.connect(settings.database_path) as db:
        db.executemany("""INSERT INTO slides (id, deck_id, slide_number, internal_slide_id, title)
            VALUES (?, ?, ?, ?, ?)""", [
            (f"extra-{number}", decks["alpha"], number, f"native-{number}", f"Extra {number}")
            for number in range(3, 53)
        ])
    page.click('[data-page="slides"]')
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 50")
    page.click("#slide-grid [data-select-slide]")
    first_id = page.evaluate("selectedSlides.values()[0].id")
    page.click("#slides-next")
    page.wait("document.getElementById('slides-page').textContent.includes('51')")
    page.click("#slide-table-view")
    page.click("#slide-table-body [data-select-slide]")
    assert page.evaluate("selectedSlides.size") == 2
    page.click("#slides-previous")
    page.wait("document.querySelectorAll('#slide-grid .slide-card').length === 50")
    assert page.evaluate(f"selectedSlides.has({json.dumps(first_id)})")
    assert page.evaluate("document.querySelectorAll('#slide-table-body [aria-pressed=true]').length") == 1
    page.click("#slide-table-body [aria-pressed=true]")
    assert page.evaluate("selectedSlides.size") == 1


def test_browser_metadata_tag_edit_and_preview_keeps_selection(page, library):
    _, _, _, slides = library
    open_slides(page)
    slide_id = slides["untagged"][0]["id"]
    page.click(f'#slide-grid [data-select-slide="{slide_id}"]')
    page.evaluate(f"openSlideDetails({json.dumps(slide_id)})")
    page.wait("document.getElementById('slide-dialog').open")
    assert "No tags assigned" in page.evaluate("document.getElementById('slide-detail-tags').textContent")
    page.click('#slide-tag-editor input[value="team"]')
    page.evaluate("document.getElementById('slide-edit-description').value = 'A manual library description'")
    page.click('#slide-edit-form button[type="submit"]')
    page.wait("document.getElementById('slide-detail-tags').textContent.includes('Team')")
    page.wait("document.getElementById('slide-edit-status').textContent.includes('Manually Edited')")
    assert page.evaluate("selectedSlides.size") == 1
    assert page.evaluate("selectedSlides.values()[0].description") == "A manual library description"
    page.click("#dialog-close")
    page.evaluate(f"openSlideDetails({json.dumps(slide_id)})")
    page.wait("document.getElementById('slide-dialog').open")
    assert page.evaluate("document.querySelector('#slide-tag-editor input[value=team]').checked")
    assert page.evaluate("document.getElementById('slide-edit-description').value") == "A manual library description"
