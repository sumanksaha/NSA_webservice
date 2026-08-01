# Module Memory: Metadata Extractor

## Purpose
Extract structured metadata (entities, fields, citations) from legal/FSO
documents using regex libraries, NER, confidence scoring, and validation.

## Responsibilities
- `regex_library.py` — large rule set for FSSAI licences, GST, phone, addresses,
  dates, etc. (13 KB — the heaviest file).
- `ner.py` — entity recognition (lightweight, regex-backed; not a heavyweight
  transformer — see dependency list).
- `engine.py` — orchestrates extraction from a `LoadedDocument`.
- `validation.py` — validate extracted fields (FSSAI format via python-stdnum,
  phone, email, GST).
- `confidence.py` — confidence scoring per field.
- `base.py` — extractor base class / interface.
- `models.py` — extracted-field dataclasses.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/metadata_extractor/__init__.py` | 1 KB | Public exports |
| `app/metadata_extractor/regex_library.py` | 13 KB | Regex rule set (largest) |
| `app/metadata_extractor/base.py` | 12 KB | Extractor interface |
| `app/metadata_extractor/engine.py` | 8 KB | Orchestration |
| `app/metadata_extractor/ner.py` | 3 KB | NER |
| `app/metadata_extractor/validation.py` | 7 KB | Validation |
| `app/metadata_extractor/confidence.py` | 2 KB | Scoring |
| `app/metadata_extractor/models.py` | 4 KB | Dataclasses |
| `app/metadata_extractor/extractors/base.py` | 3 KB | Sub-extractor base |

## Public Interfaces
- `MetadataExtractor`, `extract_metadata(document)` → `ExtractedMetadata`.

## Dependencies
regex, python-stdnum, pydantic, chardet.

## Configuration Files
- `app/metadata_extractor/regex_library.py` (rule definitions).

## Known Issues
- `extractors/base.py` exists but `extractors/__init__.py` lists only `base`
  — sub-extractor set is minimal.
- NER is regex-backed (no transformer model currently wired).

## Future Improvements
- Transformer-backed NER for entity resolution.
- Vector similarity for fuzzy field matching.

## Current TODOs
- None explicitly tracked.
