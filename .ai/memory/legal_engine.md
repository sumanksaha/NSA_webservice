# Module Memory: Legal Paragraph Detection Engine

## Purpose
Standalone, thread-safe legal-document parser (also bundled in the repo at
`legal_paragraph_detection_engine/`). Extracts structured paragraphs, clauses,
sections, citations, and hierarchical relationships from legal text.

## Responsibilities
- Pipeline: normalise text → parse sections → parse clauses → extract
  citations → detect paragraph boundaries → build hierarchical structure →
  JSON export.
- Three processing modes: FAST, ACCURATE, COMPREHENSIVE.
- Thread-safe operation (`threading.RLock` + instance cache).
- Per-paragraph confidence scoring (structure / content / citation).
- Citation extraction + preservation.

## Directory Layout
```
legal_paragraph_detection_engine/
├── src/
│   ├── legal_engine.py        # LegalParagraphEngine (entry)
│   ├── core/
│   │   ├── paragraph.py       # TextNormalizer, ParagraphBoundaryDetector
│   │   └── hierarchy.py       # HierarchyDetector, LegalNode
│   ├── parsers/
│   │   ├── section_parser.py
│   │   ├── clause_parser.py
│   │   └── legal_document.py  # DocumentTypeClassifier
│   ├── storage/
│   │   ├── citation.py        # CitationExtractor
│   │   └── exporter.py        # ParagraphExporter (JSON/CSV)
│   └── utils/
│       ├── performance.py     # PerformanceProfiler
│       ├── text_cleaner.py    # TextCleaner
├── tests/unit/                # 9 pytest modules (pytest-9.1.1)
├── examples/                  # indian_legal_texts, main.py, output_samples
├── benchmarks/
├── config/
├── output/                    # generated JSON
├── LEGAL_ENGINE_ANALYSIS_TODO.md  # EMPTY TODO (needs populating)
└── AGENTS.md                  # dir (empty)
```

## Public Interfaces
- `process_legal_document(text, config=None)` — quick entry.
- `LegalParagraphEngine(config)` → `.process_document(text)` → list[dict].

## Dependencies
Python stdlib only (re, dataclasses, enum, threading, datetime). No third-party
runtime deps — fully self-contained.

## Configuration Files
- `examples/main.py` — demo usage; `config/` — engine config.

## Known Issues
- `LEGAL_ENGINE_ANALYSIS_TODO.md` is empty — analysis backlog undefined.
- Caches cleared on every `process_document` call (may hamper throughput).
- `examples/output_samples/` and `output/` contain generated artifacts.

## Future Improvements
- Cache reuse across documents.
- RAG/vector-store integration (Qdrant) for clause-level semantic search.
- LangGraph agentic workflow orchestration.

## Current TODOs
- Populate `LEGAL_ENGINE_ANALYSIS_TODO.md` (currently empty).
