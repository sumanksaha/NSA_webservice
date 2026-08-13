# Legal-RAG Golden Benchmark v1.0 — Frozen

**BENCHMARK_VERSION = 1.0**

150-question multi-domain golden benchmark for evaluating Qdrant retrieval, Neo4j
retrieval, hybrid retrieval, provision-level enrichment, cross-domain retrieval,
authority/legal-effect identification, temporal retrieval, citation/provenance,
and hallucination/abstention behaviour.

## Artifacts

| File | Purpose |
| --- | --- |
| `benchmark_v1.0.jsonl` | 150 frozen questions (full JSONL schema, §20) |
| `gold_provisions_v1.0.json` | Deduplicated gold-provision registry (97 records) |
| `gold_sources_v1.0.json` | Canonical source documents/collections per provision |
| `benchmark_metadata_v1.0.json` | Distributions, splits, checksum, freeze record |
| `evaluation_rubric_v1.0.md` | Retrieval + evidence + generation + safety scoring |
| `review_conflicts_v1.0.json` | Agent A/B verification report (conflicts must be empty) |
| `README_v1.0.md` | This freeze statement |

## Freeze & checksum

```
BENCHMARK_VERSION = 1.0
SHA-256(benchmark_v1.0.jsonl) = a950eb46e82c58cd22c9d966cb49c3d3b15a9e38214f888ddda861ec5a480815
```

**This version is frozen and must not be modified during subsequent retrieval
optimization.** Any future change creates v1.1 / v1.2 / v2.0; do not silently
alter existing questions.

## Splits (holdout discipline, §22)

| Split | Count | Use |
| --- | --- | --- |
| dev | 100 | iterative development |
| validation | 30 | frozen validation set |
| holdout | 20 | final confirmation only — never inspect during tuning |

## Regeneration

    python build_benchmark.py

Rebuilds are deterministic (pure-hash split bucketing, no RNG); the checksum
above is stable unless content changes. The build is non-destructive: it reads
only the content modules and writes only these benchmark artifacts.
