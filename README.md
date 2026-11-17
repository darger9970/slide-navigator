# Slide Navigator

Slide Navigator is a local-first application for browsing and searching
historical PowerPoint slides and building new editable presentations. The
application provides import/review, a canonical Tag Manager, reviewed local AI
enrichment, hybrid search and native editable PowerPoint export. Product
scope, functional acceptance criteria, and the frontend visual system are
documented below.

## Product documentation

- [Product requirements and milestone acceptance criteria](docs/PRODUCT_REQUIREMENTS.md)
- [Authoritative UI design specification](docs/UI_DESIGN.md)

## Development roadmap

| Milestone | Scope |
| --- | --- |
| 2A | PowerPoint ingestion, slide extraction, thumbnails, and metadata schema |
| 2B | Decks and Slides views, filtering, and editable descriptions |
| 2B.1 | Library deletion and inline canonical tag management |
| 2C | Enhanced presentation import, progress, metadata and review |
| 2C.1 | Local Ollama AI enrichment and reviewed classification |
| 2D | Tag Manager and bulk editing |
| 3 | Keyword and local semantic search |
| 4 | Editable PowerPoint assembly and export |

Functional acceptance criteria for each milestone are maintained in
[docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md).

The consolidated 2C.1/2D/3/4 programme is tracked in
[delivery gates and architecture review](docs/PROGRAMME_DELIVERY.md). The current
programme has passed Phases A–D within the documented native-export envelope.
See [completion and acceptance evidence](docs/PROGRAMME_COMPLETION.md) for test
results, runtime verification and remaining compatibility limits.

Milestone 2C now follows the enhanced-import specification. The previous 2C
AI-enrichment scope is implemented as 2C.1; 2D, 3 and 4 are unchanged.

## Requirements

- Python 3.13 on Windows 11, or Docker on Linux
- LibreOffice Impress for legacy `.ppt` conversion and thumbnail rendering
- Ollama running locally for model status checks (optional)

No cloud API, API key, or external presentation upload is used.

PowerPoint files are preserved as managed copies by default. PPTX extraction
uses `python-pptx`. Legacy PPT conversion and slide PNG rendering use
LibreOffice Impress in headless mode through an isolated worker; Microsoft
PowerPoint, Windows COM, and desktop automation are not required. If LibreOffice
is unavailable or rendering fails, extracted slide metadata is retained and
the UI displays a local placeholder.
Slide titles come from title placeholders when available, otherwise from
conservative heading heuristics or an explicit untitled fallback. The Slides
views show the resolved presentation title or original filename, retain text
separators, and use a startup backfill to update existing records without
reimporting source files.
Deck and slide descriptions and titles can be edited in the library. Edits are
stored in SQLite and recorded in edit history; manually edited descriptions are
marked `Manually Edited` so later AI enrichment can preserve them.

### Library workflow (Milestone 2B)

Decks and Slides have collapsible sidebar filters populated from the persisted
canonical taxonomy. Decks filter by deck type and contained slide type; slides
filter by slide type and topic. Multiple selections within a category use OR;
categories and structured organisation/year/source-deck filters use AND.
Clear Filters resets the current library's filters and search, without clearing
slide selection. Counts are distinct decks/slides across the whole library,
not counts on the current page or dynamically narrowed facet counts.

Assigned tags appear in library rows/cards and details. Detail forms can assign
or remove existing canonical tags through the metadata PATCH endpoints; edits
are recorded in edit history. Custom tags are created only on explicit request.
Archived assignments remain visible and removable but cannot be newly assigned.
Inline custom-tag creation is available in Milestone 2B.1. Aliases, renaming,
merging and other lifecycle operations are available in the separate
Tag Manager (Milestone 2D). A library
without canonical tags can still be browsed and edited normally.

Click a slide thumbnail to toggle selection; use **Preview / edit** (or the
table's title) to open details. Selection spans decks and survives filtering,
sorting, pagination, grid/table switches and browser refresh.
**Create Deck (N)** opens a Selected Slides panel with drag-and-drop ordering,
keyboard-accessible Move up/down controls, removal and explicit confirmation.
Selection and its order are saved locally in the browser. Confirmation is required
again after a refresh before starting a new export. Original presentations remain
unchanged; export writes a new native PPTX.

The native export integration point is `SlideSelection.manifest()` and
the `slide-navigator:order-confirmed` document event. Its versioned payload
contains ordered `slide_id`, `deck_id`, `internal_slide_id` and `slide_number`
references. The exporter revalidates library IDs, source checksums and the OPC
dependency graph. Confirm the order, choose **Check native export**, review its
warnings, then **Generate PowerPoint**. Choose a filename and Save As destination
where the browser supports it; otherwise use the completed download action.

### Library management (Milestone 2B.1)

Decks have selectable cover thumbnails in both the table and Card view.
Selection survives filters and pagination during the current browser session.
Deck and slide **Delete Selected (N)** actions first retrieve current counts
and require confirmation. Deleting decks removes their indexed slides;
deleting individual slides updates parent counts and preserves native slide
numbers. Removed slides are also removed from the current builder selection.
Deletion is transactional and rejects stale selections/counts for review.
Original files, managed presentation copies and cached PNG files are retained;
deletion only removes library records, relationships and entity edit/enrichment
records. Import history remains, with deleted deck references cleared. Reimporting
a deleted deck is possible because its checksum association is removed.

Deck and slide cards/tables display inline canonical tags. **×** removes only
an assignment. **Add Tag** offers compatible active tags and **Create custom
tag**; custom tags reuse an existing ID for the same category and normalized
name (ignoring case and repeated whitespace). Archived tags cannot be newly
assigned or implicitly reactivated. Assignment updates are immediate, independent
transactions, recorded in edit history, and refresh library/filter/tag counts.

Management API endpoints:

- `POST /api/library/{decks|slides}/deletion-preview`: body `{"ids":[...]}`.
- `POST /api/library/{decks|slides}/delete`: body `{"ids":[...],"expected_slide_count":N}`.
  Each request accepts 1–1000 unique IDs; deletion is all-or-nothing.
- `POST /api/tags`: body `{"name":"...","tag_type":"deck_type|slide_type|topic"}`.
- `PUT` / `DELETE /api/{decks|slides}/{id}/tags/{tag_id}`: add/remove one
  assignment without replacing other assignments.

## Import configuration

### Enhanced presentation import (Milestone 2C)

Imports have two stages: ingest the entire batch, then review individual decks.
The overall bar counts presentations, including duplicates (skipped) and failed
presentations; ZIP containers are excluded. All archive discovery finishes
before the denominator is declared known. Unsupported archive members are
reported as skipped files without extraction, and archive failures are reported
separately. Recursive folder paths from the browser folder picker are retained.
Nested ZIP files are skipped rather than recursively expanded.

The second bar reports real extraction counts, resets per deck/stage and is
indeterminate until totals are known. LibreOffice conversion/rendering remains
indeterminate while its subprocess is running; no synthetic percentages are
used. Thumbnail caching then reports actual completed slide counts. Extraction
can succeed even when thumbnails are unavailable; review results show warnings.

Progress is persisted in SQLite and polled without resubmitting uploads. The
elapsed timer is based on server `started_at`/`completed_at`, runs while polling
is disconnected, and stops on terminal status. Refresh discovers active jobs
or restores the last viewed job. Result lists are paginated at 100 files.
Uploading is labelled separately from deck ingestion. Exact checksum duplicates
link to the existing deck and never overwrite its edits or review status.

Native document properties (title, creator/author, subject, comments/description,
keywords, creation/modification timestamps, last editor and other core
properties) are preserved in `source_metadata.document_properties`. An opening
slide's subtitle placeholder is kept separately in `slide_content`; ordinary
body text is not guessed to be a subtitle. Legacy PPT uses the existing
LibreOffice conversion, recorded in extraction provenance. Missing values stay
empty. Editable copies respect PATCH length limits; any truncation is recorded,
while original values remain intact.

Display-title hierarchy: meaningful native title, meaningful original filename,
opening-slide title, then original filename. Blank titles, UUIDs and common
generic titles such as “PowerPoint Presentation” are rejected as useful native
titles. The native title is always retained. Existing decks are not silently
retitled during migration.

Year precedence: a unique 1900–2100 year in the filename, a valid explicit ISO
or day/month-name/year date on the opening slide, PowerPoint creation time,
then the browser/ZIP file modification time as a clearly labelled fallback.
Ambiguous multiple filename years are not guessed. Native modification time is
not used as authorship evidence. Selected evidence, alternative candidates and
the rule are persisted in `metadata_provenance.year`; a filename's 2025 therefore
wins over native creation in 2023. This is deterministic inference for review,
not a claim of certain authorship. No organisation is guessed from filenames.

Post-import results open Deck Details for editing title, organisation, year,
author, subject, subtitle and description, canonical tag assignments and custom
tag creation. **Save and mark reviewed** persists review state/time. **Review
later** leaves imported data and review status intact; reopen through import
history or Decks. Provenance labels distinguish document properties, inferred
values, legacy unknown values and manual edits, with raw evidence expandable.
Migration 6 adds fields without rewriting existing metadata. Legacy records
remain unknown until edited; raw properties are extracted for new imports.

Milestone 2C.1 consumes immutable source content and preserves manual provenance;
its AI suggestions are stored separately until explicit review. Semantic search
and native deck assembly are delivered in programme Phases C and D.

Jobs use the existing in-process background worker, not a durable external
queue. Browser reconnects and restart recovery of staged imports are supported.
Atomic archive discovery and committed-deck checkpoints prevent duplicate work
and preserve imported metadata. Import cancellation is not exposed; index/export
jobs offer safe cancellation. Use one application worker per data directory;
distributed cross-process scheduling remains future infrastructure work.

API additions: job detail includes `deck_progress`, `current_deck`, `stage`,
server timestamps/elapsed seconds and paginated `items` (`item_offset`,
`item_limit`, maximum 500). Job history accepts `active_only=true`. Deck PATCH
adds `author` (250), `subject` (1000), `subtitle` (2000), and `review_status`
(`Not Reviewed` / `Reviewed`); raw metadata/provenance remain read-only.

The default managed storage is outside the repository at
`%LOCALAPPDATA%\SlideNavigator`. Configure the upload and ZIP expansion limits
with environment variables:

```dotenv
SLIDE_NAVIGATOR_STORAGE_MODE=managed_copy
SLIDE_NAVIGATOR_MAX_UPLOAD_FILE_BYTES=1073741824
SLIDE_NAVIGATOR_MAX_ARCHIVE_MEMBER_BYTES=536870912
SLIDE_NAVIGATOR_MAX_ARCHIVE_TOTAL_BYTES=2147483648
SLIDE_NAVIGATOR_MAX_ARCHIVE_FILES=5000
SLIDE_NAVIGATOR_WORKER_TIMEOUT_SECONDS=180
SLIDE_NAVIGATOR_RENDERING_ENABLED=true
```

ZIP entries are validated before extraction; absolute paths, traversal,
symbolic links, encrypted entries, corrupted archives, and entries over the
configured limits are rejected. Uploaded files receive a SHA-256 checksum for
exact-duplicate detection.

Browser uploads, including files selected from locally available iCloud Drive
folders, use managed copies. Browsers do not provide a trustworthy absolute
Windows source path, so Reference mode is not available for browser imports
and is explicitly rejected rather than being represented as supported.
Selecting a folder uses the browser's recursive directory picker where
available. ZIP member paths and browser-provided relative folder paths are
retained as logical source locations.

PPTX extraction and deck/slide browsing work without desktop PowerPoint.
Legacy `.ppt` conversion and PNG thumbnails require LibreOffice Impress.
Configure a non-standard executable with `SLIDE_NAVIGATOR_LIBREOFFICE_PATH`.
Rendering errors are recorded independently from extraction.

### Thumbnail rendering and regeneration

On Windows, install the official renderer with
`winget install --id TheDocumentFoundation.LibreOffice --exact` if needed.
The application and isolated worker share discovery: explicit
`SLIDE_NAVIGATOR_LIBREOFFICE_PATH`, PATH, then standard Windows LibreOffice
installation directories (including the per-user Programs location). Linux uses
the configured path or PATH; the Docker image already includes LibreOffice.

Deck Details provides **Regenerate thumbnails**. It renders the stored source
without reimporting, editing metadata/tags, restoring deleted slides, or changing
PowerPoint bytes. Original native slide numbers are used even when some slides
have been removed from the index. Work runs in the background with status and
completed-slide progress; renderer conversion is indeterminate until it returns.
One active retry per deck is reused. Failed retries retain valid existing PNGs.
Interrupted retries are marked failed on restart and can be explicitly retried.

PNG writes are atomic, and versioned image URLs plus HTTP revalidation prevent
old SVG placeholders from remaining cached. Deck covers use the first indexed
slide. The isolated worker uses `pymupdf` and reserves stdout for JSON; library
diagnostics go to stderr instead of breaking response parsing.

PDF export explicitly enables `ExportHiddenSlides` and disables notes pages.
The index includes hidden slides, so every original slide must have one PDF page
in native order; otherwise omitted hidden slides shift the preview mapping.
Regression coverage includes hidden first/last slides, exact PDF text order,
successful import previews and unchanged source bytes. The 64-slide ACN deck
exposed this case: 19 hidden slides were omitted by the default export, leaving
45 PDF pages. Hidden flags in the original PowerPoint are preserved.

Migration 9 adds `thumbnail_jobs`. `POST /api/decks/{id}/thumbnails` returns
HTTP 202 with a job ID; `GET /api/thumbnail-jobs/{id}` reports queued/processing/
completed/failed status, stage, counts and errors. Rendering disabled/missing
source/no slides return useful errors. No model or external presentation upload
is involved.

Verified locally with LibreOffice 26.8.3.1: new PPTX previews, real legacy PPT
conversion/rendering, retry after slide-index deletion, PNG persistence after
restart, and all 65 previews of the existing imported deck. All 65 PNGs decoded
at 640px width; source SHA-256 and library metadata snapshots were unchanged.
`scripts/regenerate_previews.py <deck-id>` can reproduce the regeneration and
integrity checks against the local server. Fonts and unsupported effects may
still render differently from desktop PowerPoint.

## Install

### Local AI enrichment (Milestone 2C.1)

Start local Ollama at `http://127.0.0.1:11434`. Installed `qwen3:4b` and
`qwen2.5-coder:14b` models appear in the generation dialog. No model is downloaded
automatically and no cloud provider is used. Generate AI Metadata is available
in Deck/Slide Details, for selected decks, and for the existing library.

Generation creates suggestions, never automatic metadata writes. Deck analysis
covers all extracted slide text and speaker notes; long evidence is chunked
and hierarchically summarised within a conservative context budget. Individual
slides use their own content without repeating the entire deck. Sparse slides
receive an explicit limited-analysis result without a model call or invented
visual description. No vision model is used. Deck summaries target 75–150 words,
slide descriptions 30–75; evidence may justify shorter output.

Structured JSON is constrained by a Pydantic schema, including an enum of
compatible active canonical IDs (or an empty ID list when taxonomy is empty).
Unknown IDs/wrong taxonomy fail validation. Existing tag names, aliases and
near matches are reused. Proposed new tags are separate, and created only when
the reviewer explicitly checks approval. Archived entries are not reactivated.
Tag acceptance is additive: existing assignments are retained.

Details show suggested titles, descriptions, tags, keywords/themes, organisation
evidence, model/time, limited-analysis notices and review state. Accept, Edit,
Reject and Regenerate are available. Job results also support selecting multiple
suggestions for batch acceptance; batch acceptance never creates new tags.
Manual/approved fields and fields edited while generation runs are protected,
even during acceptance. The response reports protected fields. To replace a
protected value intentionally, use the ordinary manual metadata form. Content
changed since generation requires regeneration. Original suggestions and edited
acceptance payloads are retained with review history.

Migration 7 extends `ai_enrichment_results` with fingerprint/decision/error
fields and adds `enrichment_jobs`, `enrichment_items`, `enrichment_reviews`.
Progress reports deck/slide totals, completed/failed/skipped items, current deck
and slide, persisted timestamps and elapsed time. Browser refresh resumes polling.
On application startup, interrupted enrichment items are requeued; completed
results survive and fingerprint checks avoid duplicate inference. An interrupted
HTTP generation can be repeated after a crash if no result was committed.
Use one application worker. Jobs share a model gate, run sequentially by default,
and unload their model afterwards. A different externally loaded model blocks
generation with a useful error rather than loading two large models at once.

Defaults are configurable in the generation dialog or environment:

```dotenv
OLLAMA_ENRICHMENT_MODEL=qwen2.5-coder:14b
OLLAMA_ENRICHMENT_TIMEOUT_SECONDS=180
OLLAMA_ENRICHMENT_CONTEXT_LENGTH=8192
OLLAMA_ENRICHMENT_CONCURRENCY=1
```

Allowed ranges: timeout 10–1800 seconds per model request, context 4096–32768,
concurrency 1–4 for requests to the same model. A taxonomy too large for the
chosen context fails clearly; increase the context instead of dropping evidence.
Concurrency 1 is recommended on 32 GB machines. Fingerprints include source,
taxonomy, model, context and prompt version; unchanged results (including reviewed
ones) are reused unless Regenerate is selected. Timeout/concurrency changes alone
do not invalidate content. No GPU is assumed; `/api/ps` records observed residency.

API: `GET /api/enrichment/models`; `POST /api/enrichment/jobs` with deck/slide IDs
or `all_decks`, model/options and `force`; `GET /api/enrichment/jobs` and
`GET /api/enrichment/jobs/{id}`; `GET /api/enrichment/{deck|slide}/{id}/suggestions`;
`POST /api/enrichment/review` with result IDs, accept/reject, optional edited
suggestion and explicit `approve_new_tags`. Suggestions are not verified facts.

Reproduce the real-model benchmark using
`.venv\Scripts\python.exe scripts/benchmark_enrichment.py`. It processes a
synthetic deck and two slides in an isolated temporary library, sequentially per
model, and writes [raw results and metrics](docs/OLLAMA_BENCHMARK.json). This is
separate from automated tests, which mock Ollama. Large-library throughput and
concurrency >1 require workload-specific benchmarking. No semantic search or
PowerPoint export is included.

On the measured Intel Arc 140V / 32 GB system, qwen3:4b completed the synthetic
deck plus two slides in 27.71 seconds (~27.99 generation tokens/s); the 14B
coder model took 102.40 seconds (~9.19 tokens/s). Both completed all three
items. See [benchmark conditions and limitations](docs/OLLAMA_BENCHMARK.md).

The additional business/technical/sparse scenario comparison supersedes that
speed-only default recommendation: **qwen2.5-coder:14b** is now the quality-led
default on this machine. It averaged 22.61 seconds per model-analysed text slide
versus 9.84 seconds for qwen3:4b, with better cross-slide summary coverage and
classification. Both have limitations, and some summaries were shorter than
requested. See [sample quality review and timing definitions](docs/OLLAMA_SAMPLE_BENCHMARK.md).
The model remains configurable; lighter Azure hosts should choose settings and
a locally/network-hosted Ollama model appropriate to their resources.

### Additional 2C.1 workflows and readiness

Before these additions, the existing full suite passed **97 tests**. The 2C
functional implementation and automated coverage were checked first. This is
not full environmental sign-off: real LibreOffice execution, sustained batch
throughput and manual accessibility remain unverified as noted in requirements.

Import offers an unchecked-by-default **Generate AI suggestions automatically
after import** option and installed-model selection. The choice is persisted
server-side. Only successfully imported new decks and their slides are queued;
duplicates are excluded. One enrichment job may be linked to each import, so
refresh/repeated hand-offs cannot create additional jobs. A restart recovers an
interrupted hand-off for already completed imports. An unavailable AI model
records a separate error without losing imported data. No tags or metadata are
automatically accepted. Unfinished staged ingestion now recovers on restart:
archive discovery is atomic, committed decks resume rendering, and terminal items
and manual metadata are retained without duplicate imports.

The Slides selection toolbar provides on-demand AI generation for selected
slides only. Existing individual-deck, selected-deck and library actions remain.
Detail fields display **Approved metadata**, **User edited / overridden**, or
**Existing metadata — not reviewed**. Unsaved changes are identified separately;
new AI suggestions remain in their own review panel. Edited AI suggestions
receive manual provenance, while unchanged accepted suggestions are approved AI
metadata. Both remain protected from later acceptance/regeneration overwrites.

Migration **8** stores the import opt-in/options, linked AI job and AI-step error,
adds a unique import/job association, and persists slide-field provenance.
`POST /api/import` accepts optional JSON form field `enrichment_options` (model,
include_slides, timeout_seconds, context_length, concurrency); it cannot target
unrelated existing decks or request automatic acceptance. Deck/slide detail APIs
include `metadata_states` for the UI distinctions.

The programme's Phase A increment (migration 10) adds editable approved
`keywords`/`industry` to decks/slides and `purpose` to decks, preserving suggestions
and source metadata separately. Explicit source purpose text can supply a missing
model suggestion. Organisation names require a verbatim source match; unsupported
client variants are removed from organisation/keywords/tag proposals and flagged
for review. Generated narrative remains unverified and may need human correction.
Sparse slides remain explicitly limited. The existing quality-led model choice
and earlier benchmark reports are retained.

Transient retries are configurable with `OLLAMA_ENRICHMENT_RETRIES` or per-job
`retries` (0–3, default 1). Only timeouts, connection failures and HTTP 5xx are
retried; invalid structured output and ordinary 4xx are not. The dialog exposes
the setting. Accept All names the current job and number of pending suggestions;
the server enforces `job_id` scope independently of the paginated UI. Proposed
new tags still require separate approval. Review Later/Show suggestions preserves
both suggestions and manual form edits. Approved values remain protected.

`scripts/validate_phase_a.py` separately checks the real local model against a
synthetic telecom deck without altering the user's library. Raw suggestions and
metrics are in `docs/PHASE_A_REAL_VALIDATION.json`. The interrupted overnight run
is not treated as a performance benchmark; the completed rerun is recorded there.

From the repository folder in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For legacy PPT conversion and thumbnails on Windows, install LibreOffice
Impress separately. The application does not install system software.

## Docker

The Docker image targets Linux and includes headless LibreOffice Impress:

```powershell
docker build -t slide-navigator .
docker run --rm -p 8000:8000 -v slide-navigator-data:/data slide-navigator
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Mount `/data` to persistent
storage in other Docker or Azure deployments. To check Ollama from a Docker
container, set `OLLAMA_BASE_URL` to an address reachable from that container.

## Configure local storage

By default, application data is stored outside the repository in
`%LOCALAPPDATA%\SlideNavigator`. Override individual paths with environment
variables or a `.env` file in the repository root:

```dotenv
SLIDE_NAVIGATOR_DATA_DIR=C:\Users\YourName\Documents\SlideNavigator
SLIDE_NAVIGATOR_DB_PATH=C:\Users\YourName\Documents\SlideNavigator\metadata.sqlite3
SLIDE_NAVIGATOR_IMPORTS_DIR=C:\Users\YourName\Documents\SlideNavigator\imports
SLIDE_NAVIGATOR_THUMBNAILS_DIR=C:\Users\YourName\Documents\SlideNavigator\thumbnails
SLIDE_NAVIGATOR_INDEXES_DIR=C:\Users\YourName\Documents\SlideNavigator\indexes
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_REQUIRED_MODELS=qwen3:4b,nomic-embed-text
```

The `.env` file, databases, imported presentations, thumbnails, and indexes are
excluded from Git.

## Start

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The health endpoint is
`/health`; Ollama status is available at `/api/ollama/status`.

Restart Uvicorn after changing Python code, then refresh the browser. The
command above does not automatically reload Python modules, while static
JavaScript is served from disk. An older running API can therefore reject a
newer form's fields (for example, `tag_ids` with HTTP 422). Check the running
schema at `/openapi.json` when diagnosing a frontend/API mismatch. For local
development, add `--reload` to the start command to reload Python changes.

The Settings page displays whether the local Ollama API responds and whether
each configured model is installed. It can be checked directly at
`http://127.0.0.1:8000/api/ollama/status`. A stopped or unreachable Ollama
service is reported as unavailable; it does not prevent the rest of the app
from running.

The local API includes:

- `POST /api/import` — upload multiple presentations or ZIP archives.
- `GET /api/import/jobs` and `GET /api/import/jobs/{job_id}` — import history
  and per-file progress, duplicate, and error status.
- `GET /api/decks` — searchable, filterable, sortable, paginated deck listing.
  Repeat `deck_type=<canonical-id>` and `slide_type=<canonical-id>` query
  parameters for multi-value tag filtering.
- `GET /api/decks/{deck_id}` and `PATCH /api/decks/{deck_id}` — deck details and
  persistent metadata editing.
- `GET /api/filters` — available organisation, year, and deck filters.
  Also returns active canonical `tags` with `deck_count`, `slide_count`, and
  `containing_deck_count` values. Counts include each matching entity once.
- `GET /api/slides` and `GET /api/slides/{slide_id}` — paginated slide listing
  and detail, including extracted text and notes.
- `PATCH /api/slides/{slide_id}` — persistent slide title and description edits.
  Both deck and slide PATCH bodies accept `tag_ids` to replace assignments
  with existing IDs (omit to preserve assignments; `[]` to clear). Incompatible,
  duplicate, unknown and newly assigned archived IDs return HTTP 422. Unknown
  filter IDs match no results; malformed/oversized inputs return HTTP 422.
- `GET /api/slides/{slide_id}/thumbnail` — cached PNG or a local SVG placeholder
  when rendering is unavailable.

The SQLite database is upgraded in place with additive, non-destructive
migrations. Existing presentation files, slides, thumbnails, and relationships
are preserved. It keeps deck and slide edit history, structured organisation
and year metadata, source locations, checksums, and future enrichment/tag tables.
Existing slide records receive title-source metadata and a safe title/text
backfill during startup.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```

The suite includes ingestion, metadata/migration, canonical-tag API and real
headless-browser library tests. Browser tests use installed Edge/Chrome and
`websockets` from `uvicorn[standard]`; they explicitly skip if unavailable.
Set `SLIDE_NAVIGATOR_TEST_BROWSER` to a compatible browser executable if needed.
Tests run against temporary databases, presentation fixtures and browser profiles.
Browser coverage includes thumbnail toggling, keyboard activation, selection
across pages/views/filters, ordering/removal/confirmation, canonical tag editing,
empty results and responsive layout checks. Successful real LibreOffice rendering
and manual screen-reader validation are separate environment-dependent checks.
## Canonical taxonomy administration

The Tag Manager uses canonical IDs shared with library editing and AI review.
Categories have database-validated deck/slide scopes; the initial registry adds
Industry, Technology and Client / Organisation alongside the existing categories.
Rename retains the previous name as an alias. Archive keeps assignments; deletion
removes assignments; merge transfers assignments and aliases transactionally.
Deletion and merge require a current impact preview before confirmation.
Schema 11 preserves existing IDs and takes a pre-migration SQLite backup when
upgrading schema 10. See `docs/PROGRAMME_DELIVERY.md` for acceptance evidence.

## Local search and saved selection

Search offers exact keywords, semantic retrieval and hybrid ranking, with shared
canonical category filters. Choose **Build / update local search index** once to
enable persistent incremental maintenance; Ollama needs `nomic-embed-text`.
`OLLAMA_EMBEDDING_MODEL` and `OLLAMA_EMBEDDING_TIMEOUT_SECONDS` configure it.
Changed metadata never uses an old semantic vector, while exact keyword matching
reads current fields immediately. Failures remain visible and can be retried.
Selection and order are saved in this browser's local storage and revalidated
against the library on refresh. They are not synchronised between browsers.
