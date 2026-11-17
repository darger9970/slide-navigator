# Local Ollama integration benchmark — 16 November 2026

Synthetic two-slide Northwind quarterly review, isolated temporary database;
one deck suggestion and two slide suggestions per model. No private library
content was used, and no suggestions were accepted into the user's library.

Windows 11, Intel Arc 140V integrated GPU, 33,895,956,480 bytes physical RAM
(approximately 32 GB). Each model was run sequentially at context 8192,
concurrency 1, temperature 0, with thinking disabled and schema-constrained JSON.
Models were unloaded between jobs. GPU figures below are Ollama `/api/ps`
reported residency, not independently measured dedicated video memory; this
integrated GPU uses shared memory.

| Model | Deck + 2 slides | Generation speed | Reported GPU residency | Outcome |
| --- | ---: | ---: | ---: | --- |
| qwen3:4b Q4_K_M | 27.71 s | 27.99 tokens/s | 3.88 GB | 3 completed, 0 failed |
| qwen2.5-coder:14b Q4_K_M | 102.40 s | 9.19 tokens/s | 10.43 GB | 3 completed, 0 failed |

Speed is total `eval_count / eval_duration`, excluding prompt evaluation and
model loading. Wall time includes generation/job overhead and unloading.
The fixture is short; summaries may be shorter than the target word ranges to
avoid padding sparse evidence. These numbers do not predict long-deck accuracy,
concurrent throughput, peak total system RAM or performance while other GPU
applications are active. Headless-browser tests are run separately from the
benchmark to avoid GPU scheduling contention.

The initial real runs exposed invented IDs for an empty taxonomy and incorrect
new-tag categories. Validation rejected those outputs. Canonical ID and
compatible category constraints were subsequently added to the structured
schema; the reported final runs succeeded without weakening validation.

Recommendation: use qwen3:4b, concurrency 1, context 8192 as the default on this
machine. Use the 14B model selectively and measure quality on representative
decks before accepting its roughly 3.7× higher wall time and larger memory load.
No claim of improved semantic quality is made from this small benchmark.

[Raw suggestions, metrics and resource snapshots](OLLAMA_BENCHMARK.json) are
retained. Reproduce with `python scripts/benchmark_enrichment.py`; optional model
names as arguments rerun only those models. Structured output behavior follows
the [official Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md).
