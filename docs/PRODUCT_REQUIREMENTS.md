# Slide Navigator Product Requirements

This document records the agreed product scope and functional acceptance
criteria for Slide Navigator. It is the product reference for implementation
planning. The visual system is specified separately in
[UI_DESIGN.md](UI_DESIGN.md).

## Consolidated programme delivery

The integrated 2C.1 + 2D + 3 + 4 specification is delivered as four acceptance-gated
increments. [PROGRAMME_DELIVERY.md](PROGRAMME_DELIVERY.md) records the architecture,
data-integrity and native-export risks. Existing Phase A functionality is extended
first; the four phases have now passed their documented acceptance gates.
See [PROGRAMME_COMPLETION.md](PROGRAMME_COMPLETION.md) for the requirement-by-
requirement delivery record and supported native-export envelope.

Phase A adds purpose, evidence-grounded industry and approved keywords, bounded
transient retries, job-scoped Accept All and Review Later. Manual values and
approved classifications remain protected. Test and real-model validation reports
must remain distinct. The programme is not complete merely because Phase A passes.

## Product vision

Slide Navigator is a local-first, AI-powered PowerPoint knowledge management
and deck-building application for Windows 11. It helps users preserve and
rediscover historical presentations at both deck and slide level, then reuse
slides in new editable presentations.

- Preserve original editable presentations; importing must never modify or
  delete the user's source file.
- Support local Ollama AI without requiring paid cloud APIs, API keys, or
  sending confidential presentation content to external services.
- Keep application metadata, extracted content, thumbnails, and indexes in
  configurable local storage.
- Make AI enrichment reviewable and editable; user-managed metadata remains
  authoritative.

## PowerPoint ingestion

The application must:

- Import `.pptx` and legacy `.ppt` presentations.
- Accept multiple files through drag-and-drop.
- Allow users to select individual files and browse folders recursively,
  including subfolders.
- Import ZIP archives containing presentations in nested folders.
- Support presentations stored in iCloud Drive.
- Preserve original files and record their source paths.
- Extract individual slides, slide text, available presentation and slide
  metadata, and slide thumbnails.
- Track import progress, per-file and per-slide errors, and duplicates.

Importing is an indexing operation, not a destructive move. Source
presentations must remain editable in their original format and location.
Presentation content must stay on the local device; iCloud support must not
become an external content-upload path.

## Deck management

The application must provide a dedicated **Decks** tab that:

- Lists imported presentations and shows an AI-generated, user-editable deck
  summary.
- Automatically classifies decks using the existing deck-type taxonomy,
  including Proposal, RFP Response, Case Studies, Workshop, and Credentials.
- Supports multiple descriptive topic tags per deck.
- Filters decks by year, organisation, deck type, and contained slide types.
- Preserves original metadata when available.

## Slide management

The application must provide a dedicated **Slides** tab that:

- Offers thumbnail-grid and compact-table views of individual slides.
- Shows an AI-generated, user-editable description for every slide.
- Automatically assigns one or more existing slide-type and topic tags.
- Starts with these slide types: Title Slide, Subsection, Agenda, Executive
  Summary, Approach, Team, Commercials, Case Study, and Profile.
- Shows slide counts grouped by tag type.
- Filters slides by organisation, year, deck, slide type, and topic.
- Shows the source deck and available slide metadata.

## Tag Manager

The Tag Manager must maintain separate taxonomies for:

1. Deck Type
2. Slide Type
3. Topic

It must:

- Create, rename, merge, archive, and delete tags.
- Support aliases and prevent duplicate or near-duplicate tags.
- Support bulk tag reassignment to decks and slides.
- Preserve all existing associations when a tag is renamed.
- Show usage counts for tags.

Tags classify and describe content; they are not substitutes for structured
metadata such as organisation, year, or source path.

## AI enrichment

AI enrichment must:

- Use local Ollama with `qwen3:4b` for summaries and classification.
- Reuse existing tags before suggesting new tags.
- Automatically apply suggested tags with an **AI Suggested** status.
- Flag uncertain classifications for user review.
- Let users edit generated descriptions and tags at any time.
- Never silently overwrite manually edited descriptions or metadata.
- Store user-approved corrections as local examples that can improve future
  classification.
- Reserve `nomic-embed-text` for future local semantic search.
- Keep confidential presentation content local.

Track the origin and review state of generated and edited enrichment so that
users can distinguish AI suggestions, reviewed content, and manual edits.
Model unavailability or enrichment errors must not block access to the
original presentation or already extracted local content.

## Search and deck building

The application must:

- Support keyword search across deck and slide metadata and extracted text.
- Support future semantic search using locally generated embeddings.
- Let users select slides from multiple source decks.
- Assemble selected slides into a new editable PowerPoint presentation.
- Allow generated presentations to be opened in desktop PowerPoint for final
  editing and Microsoft 365 Copilot refinement.

## Metadata and data model

Design the local data model to represent:

- Decks and individual slides.
- Organisations.
- Tags with their taxonomy type, aliases, and lifecycle state.
- Deck-tag and slide-tag assignments.
- AI enrichment results and review status.
- Import jobs, errors, duplicate detection, and progress.
- Source locations and original-file metadata.
- Edit history, including user corrections and AI-generated changes.

Keep **structured metadata** distinct from **tags**:

- Structured metadata includes values such as organisation, year, source path,
  original file name, and slide number.
- Tags are curated classifications and descriptive topics assigned from
  separate Deck Type, Slide Type, and Topic taxonomies.

Preserve original metadata when available. Model descriptions and AI
enrichment separately from source metadata so generated content cannot
silently replace the original values.

## Development milestones and functional acceptance criteria

### Milestone 2A — Ingestion, extraction, thumbnails, and metadata schema

Functional acceptance criteria:

- Users can add individual and multiple `.pptx` and `.ppt` files using browse
  and drag-and-drop.
- Users can choose a folder and the importer discovers supported
  presentations recursively within its subfolders.
- Users can import ZIP archives and the importer discovers supported
  presentations in nested folders within the archive.
- Files available through iCloud Drive can be referenced and processed without
  uploading their contents to an external service.
- The source presentation is not modified or deleted, and each imported deck
  retains its original source path.
- The local data model records decks, slides, source locations, structured
  metadata, import jobs, errors, and duplicate identity.
- Each successfully processed presentation has individual slide records with
  extracted text, available metadata, and a generated thumbnail.
- Import progress and failures are visible and attributable to the relevant
  source file. Duplicate detection is visible and does not silently overwrite
  an existing deck.
- A partial failure does not hide successfully processed files or slides.

### Milestone 2B — Decks and Slides views, filtering, and editable descriptions

Functional acceptance criteria:

- The application has distinct Decks and Slides navigation tabs.
- Decks can be listed and filtered by year, organisation, deck type, and
  contained slide types.
- Slides can be browsed in thumbnail-grid and compact-table views.
- Slides can be filtered by organisation, year, deck, slide type, and topic;
  slide counts by tag type are visible.
- Deck and slide descriptions can be edited and saved, and saved descriptions
  persist across application restarts.
- Available source metadata and tag information are shown without conflating
  structured fields with tags.
- Empty results, unavailable thumbnails, and metadata-free fields have clear
  states and do not display fabricated content.
- Decks and Slides each have collapsible sidebar menus using canonical tag IDs,
  OR within a category and AND across categories, with counts and Clear Filters.
- Assigned canonical tags are visible in cards/rows and details; existing tags
  can be assigned or removed using the metadata editing workflow. Taxonomy
  lifecycle management remains Milestone 2D.
- Slides can be selected/deselected by thumbnail, independently of opening
  preview/edit details. Selection spans source decks and survives filtering,
  sorting, pagination and grid/table changes within the browser session.
- A visible Create Deck count opens an ordered Selected Slides review with
  drag-and-drop, accessible move-up/down, removal, clearing and confirmation.
- Confirmation prepares ordered native-slide references only. It must clearly
  state that no presentation was created; actual PPTX export remains Milestone 4.

### Milestone 2B.1 — Library management enhancements

Functional acceptance criteria:

- Deck cover thumbnails have selection checkboxes, synchronized across table
  and card views and retained across pagination/filtering in the browser session.
- Deck and slide selections expose Delete Selected counts and a confirmation
  showing current affected deck/slide counts. Cancellation makes no changes.
- Library deletion is transactional, preserves all PowerPoint files, removes
  dependent index records and assignments, and refreshes counts and filters.
- Slide deletion updates parent deck counts without renumbering native slides,
  and removes deleted entries from builder selections and confirmed order.
- Inline tag controls remove only assignments, offer compatible canonical tags,
  and create custom tags while reusing IDs for normalized duplicate names.
- Changes persist, refresh taxonomy counts immediately, support keyboard
  operation, and provide success/error feedback and empty-library states.
- Full taxonomy lifecycle management remains Milestone 2D.

### Library UI refinements backlog

- [x] UI-01: Deck table descriptions wrap naturally, including line breaks and
  long tokens, without clipping or description-driven horizontal overflow.
- [x] UI-02: Deck/Slide toolbar search fields and dropdowns share control height,
  padding, spacing and row alignment at desktop and mobile breakpoints. Existing
  grid/table, filtering, selection and responsive behaviour must remain intact.

Completion requires automated real-browser layout and interaction verification.

Verified 16 November 2026: real headless-browser tests check complete description
text, multi-line layout, natural row growth, absence of clipping/overflow, shared
control dimensions/alignment and grid/table/filter interactions at 320, 650,
820 and 1280 px (description checks at 320, 650 and 1280 px). Relevant non-AI
suite: **84 passed, 2 AI-generation tests deselected**, no skips; one dependency
deprecation warning. Existing uncommitted AI work was preserved, not extended.

### Milestone 2C — Enhanced presentation import

This replaces the previous 2C label; its AI-enrichment scope is retained below
as planned 2C.1. Existing 2A, 2B, 2B.1, 2D, 3 and 4 scope remains intact.

Functional acceptance criteria:

- Batch ingestion needs no per-deck confirmation and continues after individual
  failures; imports, duplicates/skips and file/archive errors are separate.
- Overall progress counts actual presentations after archive discovery. Current
  deck progress reports actual slide extraction/caching counts, resets per deck
  and stage, and stays indeterminate for unknown counts/renderer waits.
- Elapsed hh:mm:ss uses server job timestamps, stops on terminal states, and
  remains accurate across browser refresh or a temporary connection loss.
- Polling resumes existing jobs without reuploading. Per-file results/review are
  paginated for large batches; recursive folder and archive paths are retained.
- Native PowerPoint properties are preserved with provenance, separately from
  identifiable title-slide subtitle content and editable library metadata.
- Useful native titles precede filename, opening-slide title and filename
  fallback; generic/UUID titles are not preferred. Original metadata is intact.
- Year selection prefers a unique filename year, then explicit valid opening
  slide date, document creation timestamp, and a labelled filesystem-modified
  fallback. Candidates/evidence are retained and users can override the result.
- A post-import review list opens editing for title, organisation, year, author,
  subject, subtitle, description and canonical tags, including custom tags.
- Review can be deferred or marked reviewed, with persisted status/time. Edits
  have manual provenance and duplicates never overwrite existing manual values.
- Additive migrations preserve the library and its relationships. No source
  PowerPoint is modified; PPT conversion remains LibreOffice-based and portable.
- Automated API and real-browser tests cover progress, metadata, provenance,
  review, refresh/reconnection, duplicates, partial failure and persistence.
- AI generation is excluded. The future integration reads immutable metadata
  and manual provenance and writes reviewable suggestions separately.

Detailed title/year rules and current worker limitations are documented in README.

Verification status (16 November 2026): 2C functionality is implemented and its
automated import/API/browser regression tests pass in the suite above. It is
now verified for the LibreOffice renderer: real PPTX import, legacy PPT
conversion and successful PNG previews are covered by integration tests.
The prior missing-renderer gap was resolved by installing LibreOffice 26.8.3.1,
sharing executable discovery and separating worker diagnostics from JSON.
All 65 previews of the existing deck were decoded successfully, with unchanged
source-file and metadata fingerprints. Regeneration after indexed-slide deletion
and image persistence after restart are also verified.
The 5,000-item check validates status pagination/counts, not ingestion throughput
for 5,000 real presentations. Manual accessibility validation remains outstanding.
Unfinished staged imports now resume on application restart. Archive discovery
and child publication are atomic; a deck committed before interruption resumes
rendering without duplication or metadata replacement. Deleting a committed deck
during an interrupted import prevents its recovery from resurrecting that deck.
Crash simulations cover both archive discovery and the database-commit boundary.

Thumbnail follow-up requirements:

- [x] Detect standard Windows LibreOffice installs in both app and worker, while
  retaining explicit configuration and Linux/PATH compatibility.
- [x] Keep worker stdout strictly JSON even when rendering libraries log warnings.
- [x] Include hidden slides in the rendering PDF and exclude notes pages, keeping
  one page per original slide in native order without changing source hidden flags.
- [x] Provide background thumbnail regeneration without reimport, source changes,
  metadata loss or resurrecting deleted indexed slides.
- [x] Refresh deck covers and slide previews with cache revalidation/versioning;
  retain valid cached previews when a retry fails.

### Milestone 2C.1 — Local AI enrichment and classification

Functional acceptance criteria:

- Readiness gate: check the existing 2C functionality and tests before new
  implementation. Baseline verified on 16 November 2026: **97 passed**, with
  the environmental limits in the 2C verification note retained explicitly.
- Optional automatic post-import enrichment is off by default, persisted and
  uniquely linked to newly imported decks; it survives browser refresh and
  recovers pending hand-offs after restart. AI failure does not discard imports.
- Existing decks and selected slides can be enriched on demand. Approved,
  user-edited and existing unreviewed values have separate labels; new AI
  suggestions remain separate. Neither tags nor values are automatically accepted.
- Compare installed models on business, technical and sparse scenarios before
  choosing the default. Report per-slide timing and review actual summary quality,
  including omissions and inadequate outputs; preserve the raw evidence.
- The additional quality comparison selects qwen2.5-coder:14b by default with
  concurrency 1. Runtime settings remain portable/configurable for Azure Linux.
- Enrichment progress includes deck/slide counts, current activity, frozen terminal
  elapsed hh:mm:ss and completed/failed/skipped items. Human approval stays required.

Additional workflow verification: the full suite passed **103 tests**, with no
failures/skips, followed by a passing focused restart/relink regression. One
dependency deprecation warning remains. Both real installed models completed
8/8 results across three synthetic presentations; per-slide timing and qualitative
summary limitations are recorded in `docs/OLLAMA_SAMPLE_BENCHMARK.md`. No 2D or
PowerPoint assembly work is included.

- When local Ollama and installed `qwen3:4b` or `qwen2.5-coder:14b` are available, users can generate deck
  summaries and slide descriptions and classify decks and slides.
- Classification retrieves the taxonomy, constrains IDs and reuses names/aliases
  and near matches. New tags need explicit approval and preserve taxonomy types.
- Suggestions remain separate, marked **AI Suggested**, until explicit review;
  uncertain or text-limited analyses are identified without fabricated content.
- Users can review or edit the generated descriptions and tag assignments.
- A manually edited value is preserved when enrichment is run again unless the
  user explicitly chooses to replace it.
- User-approved corrections are stored locally as examples for future
  classification.
- The UI identifies unavailable local models and enrichment errors without
  losing existing metadata or blocking manual edits.
- Presentation content remains local throughout enrichment.
- Deck/slide details support Accept, Edit, Reject and Regenerate; selected job
  suggestions support batch acceptance. Existing manual and approved fields
  remain protected, including concurrent edits. Tags are added, never replaced.
- Generation supports individual decks/slides, selected decks and the library.
  All source slide text/notes are covered using chunk/reduce for large decks.
- Models, timeout, context and concurrency are configurable. One model runs at a
  time; sequential requests are the default and observed speed/residency is recorded.
- Persisted jobs report deck/slide totals, current items, outcome counts and
  elapsed time; browser refresh and interrupted single-worker jobs can recover.
- Fingerprints prevent unchanged inference unless requested. Raw suggestions,
  model/time, review actions/edits and errors remain attributable and persistent.
- Mocked automated tests are separate from real-model benchmarking. Vision model
  support remains outside this milestone; semantic search and native export are
  delivered separately in programme Phases C and D.

### Milestone 2D — Tag Manager and bulk editing

Functional acceptance criteria:

- Users can create, rename, merge, archive, and delete tags in the separate
  Deck Type, Slide Type, Topic, Industry, Technology and Client / Organisation
  categories, plus user-created scoped categories.
- Tag aliases can be managed, and duplicate or near-duplicate tags are
  identified before they create conflicting taxonomy entries.
- Renaming a tag preserves its existing deck and slide associations.
- Users can bulk reassign tags to selected decks and slides.
- Tag usage counts reflect current assignments and update after changes.
- AI suggestions reuse existing tags where appropriate and remain distinguishable
  from reviewed and manually edited assignments.

### Milestone 3 — Keyword and semantic search

Functional acceptance criteria:

- Users can search deck and slide metadata and extracted text by keyword.
- Results identify the matching deck or slide and expose useful context such
  as slide number, source deck, and matched text.
- Users can refine results using the same organisation, year, deck, slide
  type, and topic filters used in the browsing views.
- When local embeddings are enabled, users can search semantically using
  embeddings generated with `nomic-embed-text`.
- Keyword search remains available if Ollama or semantic embeddings are
  unavailable.
- Query text and indexed presentation content remain local.

### Milestone 4 — Editable PowerPoint assembly

Functional acceptance criteria:

- Users can select slides from more than one source deck and arrange them in
  the order for a new deck.
- Users can remove or reorder selected slides before export.
- Export creates a new editable PowerPoint presentation and does not modify
  the source decks.
- The exported presentation can be opened in desktop PowerPoint for final
  editing and Copilot refinement.
- Export errors are surfaced to the user, and the selected source slides remain
  available for retry.
## Integrated programme Phase B — verified

Canonical Tag Manager: create/rename, aliases, archive/restore, search and unused
tags, possible-duplicate warnings, extensible scoped categories, usage counts,
bulk assignment/removal and impact-confirmed delete/merge. Changes preserve
canonical IDs and audit history and feed library filters and AI review.
Verified by the Phase B full gate: **120 passed**, including browser and populated
migration checks. Later integrated gates remain documented separately.

## Integrated programme Phase C — verified

Implemented persistent local embeddings, exact/semantic/hybrid search, metadata
and category filters, selectable results and selection/order persistence across
refresh. Revision tracking schedules incremental work once indexing is enabled;
changed fingerprints and deleted entities cannot use stale vectors. Shared
background jobs expose truthful counts, timestamps, failures and cancellation.
Real model compatibility and sample retrieval passed; the Phase C full gate was
**125 passed**. Measurements are recorded in `PROGRAMME_DELIVERY.md`.

## Integrated programme Phase D — verified supported envelope

Native OPC dependency copying, persistent ordered selection from library/search/
deck views, preflight review, Save As/download and recoverable export progress
are implemented. Validation checks ZIP targets, byte-preserved native parts,
slide/master identifiers, count/order and optional real LibreOffice opening.
Complex synthetic sources retain editable charts, workbooks, tables, images,
OLE objects, different themes/layouts, notes and links with exact preview matches.
Unsupported animations, relationship types and incompatible presentation-wide
settings fail explicitly. Full-envelope limits and acceptance evidence are in
`PROGRAMME_COMPLETION.md`; arbitrary PowerPoint features are not certified.
