# Slide Navigator Product Requirements

This document records the agreed product scope and functional acceptance
criteria for Slide Navigator. It is the product reference for implementation
planning. The visual system is specified separately in
[UI_DESIGN.md](UI_DESIGN.md).

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

### Milestone 2C — Local AI enrichment and classification

Functional acceptance criteria:

- When local Ollama and `qwen3:4b` are available, users can generate deck
  summaries and slide descriptions and classify decks and slides.
- Classification checks existing tags first; applied suggestions are marked
  **AI Suggested**, and uncertain results are flagged for review.
- Users can review or edit the generated descriptions and tag assignments.
- A manually edited value is preserved when enrichment is run again unless the
  user explicitly chooses to replace it.
- User-approved corrections are stored locally as examples for future
  classification.
- The UI identifies unavailable local models and enrichment errors without
  losing existing metadata or blocking manual edits.
- Presentation content remains local throughout enrichment.

### Milestone 2D — Tag Manager and bulk editing

Functional acceptance criteria:

- Users can create, rename, merge, archive, and delete tags in the separate
  Deck Type, Slide Type, and Topic taxonomies.
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
