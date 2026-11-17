# Integrated programme: delivery gates and architecture review

Source: consolidated 2C.1 + 2D + 3 + 4 specification supplied 16 November 2026.
Existing uncommitted changes are retained; no commits or pushes are authorised.

## Controlled increments

| Phase | Scope | Gate |
| --- | --- | --- |
| A | AI suggestions, human review, provenance and recoverable processing | Full regression suite plus separate real Ollama validation |
| B | Canonical Tag Manager and bulk assignments | Transaction/migration, history, API and browser tests |
| C | Incremental embeddings, hybrid retrieval and shared selection | Retrieval, stale/deleted index and browser selection tests; real embedding compatibility |
| D | Native editable assembly and download | OPC integrity, shape/chart/table/media/notes preservation, opening and visual comparison |

The baseline before Phase A changes was 111 tests passing. Earlier speed and
quality benchmarks remain in OLLAMA_BENCHMARK and OLLAMA_SAMPLE_BENCHMARK reports.
The quality-led default remains qwen2.5-coder:14b, configurable per environment.

Phase A verification: full suite **115 passed**, no failures/skips, with one
dependency deprecation warning. Subsequent focused schema/review checks passed
20 tests and the Review Later browser flow passed separately. Real local Ollama
validation completed 3/3 suggestions in **101.54 seconds** at approximately
9.77 generation tokens/second. Metadata/title were not accepted or overwritten.
The first overnight run was interrupted by machine suspension and is excluded
from performance reporting. Raw evidence is in PHASE_A_REAL_VALIDATION.json.
Short summaries and model classification quality still require human review;
schema validation is not a factual-accuracy guarantee.

Current checkpoint: Phase A technical acceptance passed. The user authorised
completion of the remaining programme after the architecture explanation.
Phase B implements a registry-backed taxonomy migration, dedicated manager,
aliases, archive/restore, impact-confirmed merge/deletion and bulk assignments.
Phase B full gate: **120 passed**, no failures or skips (141.01 seconds).
Phase C adds persistent little-endian float vectors in SQLite, fingerprint-based
incremental updates, revision triggers, observable/recoverable background jobs,
literal keyword and local semantic search, category filters and shared persisted
selection. The portable flat cosine index suits the current local library; larger
installations should benchmark an ANN index. Keyword scoring scans current fields
directly rather than relying on an asynchronously refreshed FTS snapshot.
Hybrid ranking combines keyword and cosine result ranks with RRF k=60.
No stale fingerprint participates in semantic retrieval; deletion removes vectors.
Once explicitly enabled, incremental maintenance resumes after restart. Failed
items are visible and require retry; cancellation retains valid completed vectors.
Phase C focused tests: **5 passed**, including browser refresh/selection.
Real nomic-embed-text: **6/6 documents**, 768 dimensions, **3.778 seconds** total;
three representative queries ranked the expected presentation first, with query
times 0.045–0.047 seconds. This synthetic compatibility sample is not a broad
retrieval-quality benchmark. Evidence: `PHASE_C_REAL_VALIDATION.json`.
Protocol: https://docs.ollama.com/api/embed; task prefixes follow
https://huggingface.co/nomic-ai/nomic-embed-text-v1.5.
Phase C full gate: **125 passed**, no failures/skips (199.21 seconds).
Phase D full gate: **132 passed**, no failures/skips (175.67 seconds), with native
multi-source object/package tests and a separate real LibreOffice opening/visual
comparison: both rich slides exactly matched their source previews at 640×480.
Export including real opening validation took 5.824 seconds. Raw evidence:
`PHASE_D_REAL_VALIDATION.json`.

Final audit closes unfinished-import restart recovery: archive child publication
is atomic, committed decks resume rendering without duplicate classification,
terminal accounting is idempotent, manual metadata stays intact and deleting a
deck during interruption prevents resurrection. Four crash/deletion tests pass.
Final complete regression gate: **136 passed**, no failures/skips, one dependency
deprecation warning, **179.48 seconds**. Runtime migration and restart preserve
all existing library row hashes and original PowerPoint hashes; real-library
browser checks pass separately. See [completion report](PROGRAMME_COMPLETION.md).

## Phase A additions

- Migration 10 adds approved keywords, purpose and industry to existing metadata
  records; raw source properties and suggestions remain separate.
- Industry generation requires source evidence. Sparse slides remain limited.
- Slide analysis receives bounded native title/subject context without repeating
  the whole source presentation for every slide.
- Transient connection/timeout/5xx retries are configurable (0–3). Schema errors
  and ordinary 4xx errors are not retried automatically.
- Accept All has a server-enforced job scope and pending-only semantics, without
  the explicit-result-list size limit. It does not create proposed tags. Individual
  selected review, Edit/Reject/Regenerate and protected metadata remain available.
- Review Later hides suggestions without deleting them or resetting manual form
  contents. Purpose/industry/keywords can be edited through validated APIs/forms.
- A new job clears old progress; terminal-status checks replace ambiguous checks
  for the count label “completed”.

## Significant architecture risks before later phases

The specification explicitly says: “Stop and explain significant architectural
risks before making major changes, especially around native PowerPoint slide
assembly.” These are the review points before those changes.

### B: extending the canonical taxonomy

The existing tags table has a CHECK constraint permitting only deck_type,
slide_type and topic. Extensible categories cannot be added by changing frontend
labels or accepting arbitrary strings. Proposed approach:

1. Add a category table with stable IDs, labels and allowed deck/slide scopes.
2. Transactionally rebuild the tags table with a foreign key to that registry,
   preserving every tag ID, normalized name, status and timestamp.
3. Preserve assignments, aliases and history; validate foreign keys and row/ID
   counts against a backup before releasing the migrated database.
4. Add Industry, Technology and Client/Organisation as tag categories. Keep the
   existing structured organisation/year fields authoritative; classification
   tags do not become a competing metadata store.
5. Implement merge/delete impact previews and atomic revalidation on confirmation.
   Merge rewrites assignments/aliases to the surviving ID and records history.

SQLite table reconstruction is a significant data-integrity operation: a naive
DROP with foreign keys enabled would cascade-delete assignments. Migration must
be exercised on populated old-schema fixtures and a backup copy before touching
the real library. No taxonomy rebuild has been applied by Phase A.

### C: search/index consistency

Proposed approach: SQLite FTS5 for lexical retrieval plus persistent float-vector
records with model/content fingerprints. A dirty queue maintained by database
triggers covers imports, metadata/tag edits and deletions. Read-time fingerprint
checks must exclude stale vectors immediately; embeddings update in recoverable
jobs rather than blocking metadata writes. Model/dimension changes invalidate
only incompatible vectors. Begin with a portable flat cosine scan for local
libraries and measure before introducing a platform-specific ANN dependency.

Use reciprocal-rank fusion (documented rank positions, not fabricated relevance
percentages) and existing OR-within/AND-across filter conventions. Shared selection
must persist ordered IDs and revalidate deleted records after refresh. Real
nomic-embed-text compatibility and retrieval quality must pass before Phase D.

### D: native PowerPoint assembly

Copying a slide XML element is insufficient. The dependency graph can include
layouts, masters, themes, notes, charts and embedded workbooks, diagrams, images,
media, OLE objects, links and extension parts. Relationship identifiers are local
to each part; part names and content types must be remapped consistently.

Proposed initial acceptance envelope:

- Traverse/copy complete OPC dependency graphs with a visited map, allocate
  collision-free names, rewrite internal relationship targets and rebuild the
  presentation/master/slide lists in the requested order.
- Copy object/package bytes rather than using rendered slide images. Use
  python-pptx as an inspection tool, not a claim of complete native cloning.
- Reject incompatible slide dimensions rather than silently distorting content:
  a destination presentation has one global slide size.
- Reject missing relationship targets, macros/unsafe package structures and
  unsupported relationships. Report animations, external resources, internal
  links to unselected slides, fonts and embedded content limitations explicitly.
- Treat legacy PPT conversion as a separate fidelity boundary. Never replace or
  alter the original file. File-picker/download output is a new .pptx.
- Validate ZIP/content types/relationships, object editability, slide order and
  source checksums; render the output with LibreOffice and compare selected
  source/output slides. Office-specific behavior cannot be certified solely by
  successful LibreOffice opening.

This cannot honestly guarantee universal fidelity for arbitrary PowerPoint
extensions. A fail-closed assembly prototype and complex multi-source fixtures
must establish the supported envelope before a production export success claim.

Primary references:

- [Presentation structure](https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-presentations)
- [Slide masters and layout relationships](https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-slide-masters)
- [Slide/layout/master associations](https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-slide-layouts)
