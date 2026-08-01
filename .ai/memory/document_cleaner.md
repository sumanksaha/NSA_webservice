# Module Memory: Document Cleaner

## Purpose
Post-ingestion text cleaning pipeline: remove boilerplate, normalise whitespace,
differ/track changes, and produce cleaning statistics for legal documents.

## Responsibilities
- `removers.py` — strip headers, footers, page numbers, signatures, etc.
- `normalizers.py` — whitespace/unicode normalisation, line-joining heuristics.
- `differ.py` — compute diffs between original and cleaned text.
- `pipeline.py` — orchestrate cleaners in order with config.
- `stats.py` — emit cleaning statistics (chars/words removed).
- `config.py` — cleaner toggle switches.
- `models.py` — cleaning-result dataclasses.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/document_cleaner/__init__.py` | 1 KB | Public exports |
| `app/document_cleaner/config.py` | 2 KB | Settings |
| `app/document_cleaner/removers.py` | 10 KB | Boilerplate removal |
| `app/document_cleaner/normalizers.py` | 8 KB | Text normalisation |
| `app/document_cleaner/differ.py` | 4 KB | Diff tracking |
| `app/document_cleaner/pipeline.py` | 6 KB | Orchestration |
| `app/document_cleaner/stats.py` | 3 KB | Statistics |
| `app/document_cleaner/models.py` | 5 KB | Dataclasses |

## Public Interfaces
- `CleaningPipeline`, `CleanedDocument`, `CleaningStats`.

## Dependencies
re, pathlib, unicodedata (stdlib); pydantic.

## Configuration Files
- `app/document_cleaner/config.py` central config.

## Known Issues
- Heuristic-based removal may drop legitimate content near headers/footers.

## Future Improvements
- ML-based boilerplate detection.

## Current TODOs
- None tracked (module appears mature).
