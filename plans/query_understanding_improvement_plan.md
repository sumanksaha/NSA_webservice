# Query Understanding Improvement Plan

## Current State Analysis

The query understanding module (`app/rag/retrieval/query_understanding.py`) serves as a single seam for parsing user queries into multiple views:

- **Legacy 5-way view**: `query_type` (QueryType enum) + `parsed_filters` (dispatch to SectionQueryParser/AuthorityQueryParser/CaseLawQueryParser/JurisdictionQueryParser)
- **Legal 14-type view**: `legal_type` (string) + `legal_confidence` (from `classify_with_confidence`)
- **Entity views**: `act`, `section`, `subsection`, `authority`, `citation`, `court`, `jurisdiction`, `jurisdiction_level`
- **Identifier arm**: `identifier_query`, `identifier_meta`
- **Form references**: `form`, `form_query`, `target_query`

### Known Failure Modes (from code inspection and test analysis)

1. **Query type classification gaps** (`query_classifier.py:93-153`):
   - `_QUERY_PATTERNS` only covers 5 categories: amendment, section, case law, provision, general
   - Queries not matching these patterns default to `GENERAL_QA`
   - Missing: `penalty`, `prohibition`, `authority`, `procedure`, `cross_reference`, `obligation` types from the `QueryType` enum
   - **Impact**: `test_legacy_view_matches_classifier_and_parser` fails for non-covered query types

2. **Legal type classifier keyword overlap** (`legal_query_classifier.py:254-455`):
   - `_TYPE_PATTERNS` has ordering issues where broader types can swallow more specific ones
   - `procedure` type (line 391-403) includes `tribunal` which also appears in `authority` list (line 314-333)
   - `obligation` type (line 416-428) includes `shall` which is a very common word
   - **Impact**: Misclassification of queries, incorrect reranker configuration

3. **Entity detection limitations** (`query_understanding.py:169-178`):
   - `detect_act`, `detect_section`, `AuthorityQueryParser.parse`, `CaseLawQueryParser.parse`, `JurisdictionQueryParser.parse` are rule-based
   - `AuthorityQueryParser.parse` (query_classifier.py:241-258) uses exact authority name matching with fuzzy fallback
   - Missing: fuzzy matching, synonym handling, partial name matching
   - **Impact**: Authority/jurisdiction/entity extraction fails on varied query phrasing

4. **QueryParser dispatch gaps** (`query_classifier.py:358-371`):
   - `_PARSERS` dict only maps 5 query types: `SECTION_LOOKUP`, `AMENDMENT_QUERY`, `PROVISION_SEARCH`, `CASE_LAW`, `GENERAL_QA`
   - Missing mappings: `IDENTIFICATION`, `LOOKUP`, `DEFINITION`, `PROHIBITION`, `DUTY`, `RIGHT`, `POWER`, `PENALTY`, `EXCEPTION`, `PROCEDURE`, `APPLICABILITY`, `COMPARISON`, `TEMPORAL`, `JURISDICTION`, `CROSS_REFERENCE`, `AUTHORITY`, `FACT_PATTERN`, `COMPLIANCE_ASSESSMENT`, `AMENDMENT_QUERY` (legacy), `PROVISION_SEARCH` (legacy)
   - **Impact**: `parsed_filters` is empty dict for unmatched query types

5. **Form reference detection** (`query_understanding.py:193`):
   - `detect_form` is imported from `form_references` but its behavior is not fully visible
   - Form number aliases (notice->v, analyst report->vii, appeal->viii) are in `workflow_query_recognizer.py` but not integrated into the main `QueryUnderstanding` model
   - **Impact**: Form references silently dropped for non-workflow queries

6. **Empty/ambiguous query handling** (`query_understanding.py:164, legal_query_classifier.py:474-475`):
   - Both modules return "general"/"ambiguous" for empty queries
   - No consistent handling across the pipeline
   - **Impact**: Downstream consumers may not handle ambiguous state gracefully

## Improvement Proposals

### 1. Expand QueryType Classification Patterns
**File**: `app/rag/retrieval/query_classifier.py`
- Add missing `QueryType` enum values to `_QUERY_PATTERNS`
- Reorder patterns to prevent broader types from swallowing specific ones
- Add `procedure`, `penalty`, `prohibition`, `authority`, `cross_reference`, `obligation` patterns
- **Source reference**: `QueryType` enum definition at `query_classifier.py:31-58`

### 2. Fix Legal Type Classifier Ordering and Specificity
**File**: `app/rag/retrieval/legal_query_classifier.py`
- Reorder `_TYPE_PATTERNS` to place more specific types before broader ones
- Resolve conflicts: `tribunal` in both `authority` and `procedure`, `shall` in `obligation`
- Add quoted-term guard for `definition` type (already present at line 375)
- **Source reference**: `LEGAL_QUERY_TYPES` at `legal_query_classifier.py:42-57`

### 3. Enhance Entity Detection with Fuzzy Matching
**File**: `app/rag/retrieval/query_classifier.py` and `app/rag/retrieval/identifier.py`
- Add fuzzy authority matching (Levenshtein/edit distance for partial name matches)
- Improve `JurisdictionQueryParser.parse` with state alias handling
- Add case-law citation pattern improvements
- **Source reference**: `_KNOWN_AUTHORITIES` at `query_classifier.py:179-194`

### 4. Complete QueryParser Dispatch Mapping
**File**: `app/rag/retrieval/query_classifier.py`
- Map all `QueryType` enum values to appropriate parsers in `_PARSERS` dict
- Create dedicated parsers for: `PENALTY`, `PROHIBITION`, `PROCEDURE`, `AUTHORITY`, `CROSS_REFERENCE`, etc.
- **Source reference**: `QueryType` enum and `_PARSERS` at `query_classifier.py:361-367`

### 5. Integrate Form References into QueryUnderstanding
**File**: `app/rag/retrieval/query_understanding.py`
- Ensure `detect_form` results are consistently captured
- Add form reference detection to the main `understand()` flow
- Integrate workflow form aliases for non-workflow queries
- **Source reference**: `form` and `form_query` fields in `QueryUnderstanding` dataclass at `query_understanding.py:123-125`

### 6. Standardize Empty/Ambiguous Query Handling
**File**: `app/rag/retrieval/query_understanding.py` and `app/rag/retrieval/legal_query_classifier.py`
- Ensure consistent "general" / "ambiguous" classification across modules
- Add documentation for expected behavior
- **Source reference**: `normalize_query_type` at `query_understanding.py:61-76`, `classify_legal_query` at `legal_query_classifier.py:463-489`

### 7. Add Comprehensive Test Coverage
**File**: `tests/test_query_understanding.py`
- Add queries for each `QueryType` enum value
- Add queries for legal types not yet tested
- Add edge cases: ambiguous queries, mixed-type queries, empty queries
- **Source reference**: Existing `QUERIES` list and test patterns at `test_query_understanding.py:21-32`

## Validation Plan

After implementing the improvements:

1. Run `tests/test_query_understanding.py` - all 11 tests should pass
2. Verify `QueryType` classification for all enum values
3. Verify `legal_type` classification for diverse query patterns
4. Verify entity extraction (act, section, authority, jurisdiction) on varied inputs
5. Verify `QueryParser` dispatch works for all query types
6. Verify form reference detection across query types
7. Run integration tests: `tests/_test_workflow_part1.py`, `tests/test_food_intent_retrieval.py`