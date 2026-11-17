# Representative-scenario comparison — 16 November 2026

Three synthetic presentations: a two-slide business review with numerical
results and approval caveats, a two-slide technical architecture review with
explicit deployment limitations, and a sparse one-word roadmap slide. Active
canonical taxonomy was provided. This covers useful scenarios but is not a
production-corpus benchmark. No confidential library content was used.

Both models ran sequentially, at context 8192 and concurrency 1, in isolated
temporary databases. No metadata or tags were accepted. Each completed 3 deck
and 5 slide results without model/validation failures; sparse deck/slide results
were explicitly limited and required no model inference.

| Model | Whole job | Mean per slide (5, including sparse) | Mean per text slide (4 model calls) |
| --- | ---: | ---: | ---: |
| qwen3:4b | 62.66 s | 7.87 s | 9.84 s |
| qwen2.5-coder:14b | 149.98 s | 18.10 s | 22.61 s |

Slide timings are measured item durations, including each model request and
persistence. Whole-job time includes deck analysis and model loading/unloading;
dividing whole-job time by slide count would not measure slide inference.

## Summary quality review

The following is a qualitative review of the retained outputs against the exact
synthetic references, not a statistical score or an independent human study:

- **4B:** the technical deck description was only four words. The business
  summary largely repeated the first slide, missing next-step coverage. It
  suggested Architecture for the business review and emitted long paragraph
  fragments as organisation names. Schema-valid output was not necessarily
  useful classification or extraction. Sparse-slide handling was correct.
- **14B:** both substantive deck summaries covered content from both slides.
  Technical Architecture and business Operations tags were more appropriate.
  Northwind was extracted as a concise organisation; the technical organisation
  was omitted rather than returned as a paragraph. It preserved the business
  approval caveat, but omitted some detail such as the precise 12% figure and
  explicit Azure/semantic-search limitations. Summaries were roughly 44–49 words,
  below the 75–150-word target. Sparse-slide handling was correct.

**Default choice: qwen2.5-coder:14b**, concurrency 1, for better coverage and
classification in this sample, despite roughly 2.3× slower text-slide processing.
qwen3:4b remains selectable for faster generation. The previous speed-only
two-slide benchmark recommendation is superseded by this quality comparison.
Manual/approved metadata and tags remain untouched until explicit review.

This does not establish general accuracy, peak memory, concurrent throughput or
Azure performance. The previously measured Arc 140V/32 GB resource observations
are retained in [the earlier benchmark](OLLAMA_BENCHMARK.md). No Windows-specific
runtime dependency was introduced.

Reproduce with `python scripts/benchmark_sample.py`; optional supported model
names rerun only those entries. [Raw outputs, references and metrics](OLLAMA_SAMPLE_BENCHMARK.json)
are retained for review. The initial 4B run succeeded, then its harness cleanup
hit a Windows SQLite file lock; explicitly closing the seed connection fixed
the harness, and the 14B run then completed normally.
