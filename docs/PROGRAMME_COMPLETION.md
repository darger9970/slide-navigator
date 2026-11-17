# Integrated programme delivery — 17 November 2026

The consolidated brief's Phases A–D are implemented within the native-export
compatibility envelope below. This is a local application delivery, not an Azure
deployment or a certification of every PowerPoint feature. No commit or push was
performed, and existing uncommitted work was retained.

## Acceptance gates

| Gate | Result | Separate real integration evidence |
| --- | --- | --- |
| Phase A — reviewed AI enrichment | 115 passed | 3/3 suggestions, 101.54 s, approximately 9.77 generation tokens/s |
| Phase B — canonical Tag Manager | 120 passed, 141.01 s | Real headless-browser manager/create/archive flow; populated migration preserves IDs, aliases and assignments |
| Phase C — local hybrid search | 125 passed, 199.21 s | nomic-embed-text: 6/6 documents, 768 dimensions, 3.778 s; three expected top-ranked results, 0.045–0.047 s/query |
| Phase D — native editable export | 132 passed, 175.67 s | Two rich multi-source slides exported/opened with LibreOffice in 5.824 s; both source/output previews exactly match at 640×480 |
| Final recovery/integration gate | 136 passed, 179.48 s | Existing-library migration/restart and read-only browser checks also passed |

No tests failed or skipped at these gates. One dependency deprecation warning
concerns Starlette TestClient's httpx integration. Earlier failed development
runs exposed and corrected duplicate UI registration, test polling assumptions
and an order check that incorrectly relied on python-pptx's renamed part paths.
They are not counted as successful acceptance runs.

Real-model tests use synthetic presentation content and are distinct from mocked
deterministic tests. LibreOffice opening and pixel comparison are automated real
integration checks, not a claim of manual Microsoft PowerPoint testing.

Evidence:

- [AI sample benchmark](OLLAMA_SAMPLE_BENCHMARK.md) and [Phase A real run](PHASE_A_REAL_VALIDATION.json).
- [Phase C real embedding/retrieval run](PHASE_C_REAL_VALIDATION.json).
- [Phase D native/LibreOffice/visual run](PHASE_D_REAL_VALIDATION.json).
- [Existing library migration and restart](PROGRAMME_RUNTIME_VALIDATION.json).
- [Existing library browser verification](PROGRAMME_RUNTIME_BROWSER.json).

The existing live library contains one deck and 17 indexed slides. Its original
deck/slide/tag/assignment/alias/edit-history row hashes and original PowerPoint
hashes remained unchanged through migration from schema 10 to 12 and restart.
Foreign-key validation returned no errors. Browser verification covered deck and
slide grid/table views, taxonomy, exact search, refresh-persistent selection and
the ordered builder review without editing the library.
All 17 indexed thumbnails decoded as actual PNG previews rather than placeholders.
The updated application was left running at `http://127.0.0.1:8000`.

## Consolidated requirements 1–12

| Brief requirement | Delivered behaviour / verification |
| --- | --- |
| 1 — Objective | Integrated import → optional AI suggestions → review/classification → discovery → selection/order → editable PPTX export. |
| 2 — Existing implementation | FastAPI, SQLite, HTML/CSS/JS and all earlier library/import workflows retained; original PowerPoint files remain read-only. |
| 3 — AI enrichment | Optional post-import and on-demand deck/slide jobs, bounded context/chunking/retries, configurable model/concurrency, durable recovery, protected manual/approved fields and human acceptance/rejection/edit/regeneration. Purpose, grounded industry and approved keywords persist separately from raw properties and suggestions. |
| 4 — Canonical Tag Manager | Dedicated administration; scoped extensible categories; create/rename/archive/restore; aliases; counts/search/category/unused filters and possible-duplicate warnings; bulk assign/remove; transactional merge/delete with an impact token that rejects stale confirmation; audit history and stable canonical IDs. |
| 5 — Search | Current metadata/text/notes/tags/keywords/organisation/industry/year; exact/semantic/hybrid modes; RRF k=60; structured filters; OR within a category, AND across categories; selectable results; persistent local vectors and incremental maintenance after enablement. Stale/deleted vectors cannot participate. |
| 6 — Deck Builder | One persisted ordered tray across library, search and individual-deck views; native OPC graph assembly; filename and browser Save As/download; review/preflight; observable progress; source checksum protection; validation and explicit unsupported-content errors. |
| 7 — Background processing | Existing ingestion/enrichment mechanisms retained; shared durable work jobs for indexing/export; counts, stages, timestamps, failures, polling/reconnection and restart recovery. Index/export cancellation occurs at safe boundaries. Requests and worker claims prevent duplicate execution. |
| 8 — Architecture/data | Migrations 11/12, stable IDs and foreign-key checks; SQLite vectors and filesystem artifacts remain local and Linux compatible; no COM/desktop PowerPoint dependency. |
| 9 — UX | Existing orange accent and responsive library design retained; description wrapping and toolbar alignment remain tested; separate selection and edit controls, keyboard ordering, loading/errors and destructive-action confirmation. |
| 10 — Testing | Full gated regression suites; deterministic mocked AI/retrieval tests; real Ollama tests; real headless-browser tests; native package/object tests and real LibreOffice visual comparisons. |
| 11 — Sequence | A passed before B, B before C, and C before D. Architecture risks were explained and documented before major changes; the user authorised continuation. |
| 12 — Reporting | Requirements/backlog, README, delivery risks, raw validation evidence and this report updated. No GitHub publication. |

## Database and APIs

- Migration 11 adds `tag_categories` and `taxonomy_history` and rebuilds `tags`
  with a category foreign key, preserving existing rows/references. A schema-10
  upgrade creates a `.before-v11.sqlite3` backup beside the database.
- Migration 12 adds `work_jobs`, `work_items`, `search_vectors` and revision/delete
  triggers. Embeddings are little-endian float vectors with model, dimension and
  source fingerprint. No new dependency on a platform-specific vector service.
- Taxonomy: `GET /api/taxonomy`, category creation, tag PATCH, alias creation,
  impact/apply operations, bulk assignments and history. Existing `/api/tags` and
  assignment endpoints continue to use canonical IDs. Library filters accept
  category-grouped `tag_id` parameters.
- Search: `GET /api/search`, `POST /api/search/index`; index maintenance starts
  only after explicit enablement. Metadata writes do not block on embeddings.
- Work: job history/status and safe cancellation under `/api/work-jobs`.
- Export: `/api/exports/preflight`, `/api/exports` and job-specific download.
  Source/selection fingerprints and request UUIDs protect review and retries.
- Import recovery adds no schema migration. Archive child publication is atomic;
  a new deck links to its import item in the deck transaction. Terminal-item
  accounting is idempotent, and resumed work preserves the original start time.

## Main files changed

| Area | Files |
| --- | --- |
| Taxonomy | `app/taxonomy.py`, `app/taxonomy_migration.py`, `app/library_tags.py`, `app/library_management.py`, `app/static/taxonomy.js` |
| Search/jobs | `app/search.py`, `app/work_jobs.py`, `app/static/search.js`, `app/static/selection.js` |
| Native export | `app/assembly.py`, `app/static/builder.js` |
| Existing integration | `app/main.py`, `app/database.py`, `app/config.py`, `app/enrichment.py`, `app/ingestion.py`, `app/static/app.js`, `management.js`, `ai.js`, `index.html`, `styles.css` |
| Regression coverage | `tests/test_taxonomy.py`, `test_search.py`, `test_assembly.py`, `native_fixtures.py`, `test_import_recovery.py`; migration expectations and import polling wait in existing tests |
| Evidence/documentation | README, product requirements, programme delivery/completion reports, Phase A/C/D and runtime validation JSON; scripts `validate_phase_a.py`, `validate_phase_c.py`, `validate_phase_d.py`, `validate_runtime.py`, `validate_runtime_browser.py` |

## Native-export support and explicit limits

The assembler copies slide XML and its native relationships/dependencies, not
slide screenshots. Tests retain text, formatting, shapes, tables, charts,
embedded XLSX workbooks/OLE objects, images, separate masters/layouts/themes,
speaker notes and HTTPS hyperlinks. Native payload bytes are validated unchanged;
master layout IDs and relationship targets are deliberately remapped. Slide count
and order are checked from the actual presentation relationships. LibreOffice
opens every exported slide when rendering is enabled.

Exports explicitly reject mixed slide/notes dimensions, incompatible global
default text styles, differing notes-master designs, conflicting custom table
styles or embedded typefaces, animations with timing trees, ActiveX controls,
signed/macro-bearing packages, unsupported dependency types, missing targets,
custom table styles with dependent parts, unsafe external linked media, and
internal slide links whose destination is not selected. Nothing is silently
flattened or omitted. Legacy PPT uses the existing LibreOffice conversion worker;
the preflight warns that binary-PPT conversion is a separate fidelity boundary.

External web/mail hyperlinks and system-font availability are disclosed at
preflight. Fonts can alter appearance on another machine. Embedded workbook
bytes are retained, but arbitrary embedded applications/media/SmartArt/Office
extensions are not all independently exercised. Generated PPTX has not been
manually opened in desktop Microsoft PowerPoint, and universal fidelity is not
claimed. Presentation-wide custom shows, sections and handout settings are not
exported as features of the newly assembled deck.

## Operational limits and next milestone

- Run one application process/container per SQLite data directory. The current
  recoverable workers are in-process; distributed multi-worker scheduling is not
  implemented. Azure Linux compatibility is architectural and Docker-based;
  actual Azure deployment was not performed.
- Browser-local selection/order is not synchronised between devices. Browser
  refresh loses an unsaved native file handle; the completed export still offers
  Save As/download. Fully staged imports recover; an upload interrupted before
  staging finishes cannot recover bytes never received from the browser.
- Flat cosine/keyword scanning is appropriate to the present local library.
  Production-scale ingestion/retrieval benchmarking on 5,000 real presentations
  and manual screen-reader/accessibility assessment remain unperformed validation
  activities. These are not represented by the 5,000-item status-pagination test.
- AI quality is not guaranteed by valid JSON. Human review remains mandatory;
  benchmark samples are small and synthetic. No suggestion is automatically
  accepted or allowed to replace approved/manual metadata.

Recommended next milestone: production acceptance and hardening with a supplied
representative deck corpus—broader native-feature compatibility, Microsoft
PowerPoint opening/fidelity review, retrieval-quality/load measurements,
accessibility assessment and an Azure Linux deployment rehearsal.
