# CHUNK AUDIT — Existing FSSAI RAG Corpus

> Source: `backup:backups/vector_store_fssai_legal_768_20260809_161941.json` · audit v1.0 · generated 2026-08-10T01:04:27+00:00

## Summary

| Metric | Value |
| --- | ---: |
| Total chunks | 12819 |
| Unique chunk IDs | 12819 |
| Unique documents | 29 |
| Document types | `{"regulation": 8661, "act": 4065, "notification": 93}` |
| Chunks/doc (min/mean/max) | 6 / 442.0 / 5913 |

## Severity-classified findings

| Metric | Count | % of corpus | Severity |
| --- | ---: | ---: | --- |
| duplicate_chunk_ids | 0 | 0.0% | CRITICAL |
| empty_chunks | 0 | 0.0% | CRITICAL |
| broken_payloads | 0 | 0.0% | HIGH |
| missing_document_id | 0 | 0.0% | HIGH |
| missing_document_uri | 0 | 0.0% | HIGH |
| missing_page_info | 12819 | 100.0% | HIGH |
| missing_section_info | 9626 | 75.09% | HIGH |
| missing_content_hash | 0 | 0.0% | MEDIUM |
| unusually_short_chunks | 7669 | 59.83% | MEDIUM |
| chunks_with_citations | 3011 | 23.49% | LOW |
| chunks_with_entities | 0 | 0.0% | LOW |
| chunks_with_references | 0 | 0.0% | LOW |
| chunks_with_section_metadata | 3193 | 24.91% | LOW |
| duplicate_content_chunks | 4344 | 33.89% | LOW |
| duplicate_content_groups | 645 | 5.03% | LOW |
| incomplete_sentence_chunks | 5431 | 42.37% | LOW |
| missing_required_payload_key | 0 | 0.0% | LOW |
| multi_provision_chunks | 117 | 0.91% | LOW |
| total_chunks | 12819 | 100.0% | LOW |
| unique_chunk_ids | 12819 | 100.0% | LOW |
| unusually_long_chunks | 38 | 0.3% | LOW |

## Character-length distribution

min=1 · p50=57 · mean=174.5 · p90=434 · p99=1628 · max=7368

## Exemplars (first 5 per metric)

### multi_provision_chunks

* `04425b33-f059-4715-94a0-d77c8d5d9e35: section 189, sub-section (2) of section 190,`
* `076ac54a-25e5-471c-aa73-6df2ae0ff329: section 28 and section 28A", the word and figures`
* `0d8f99e0-a7d7-41ca-ab9c-d28ecc2bddfb: 82 THE GAZETTE OF INDIA : EXTRAORDINARY [PART III—SEC. 4] 'FORM D-2' (See Regulation 2.1.13) Half Yearly Return for Milk and Milk Products F`
* `0faeaa79-2cbb-4b80-8b45-48fd083baaee: Food Businesses) Amendment Regulations, 2015, were published as required under sub-section (1) of section 92 of the Food Safety and Standard`
* `0fd9b041-f5f8-4a46-ae25-ab211a521f5b: provided in Annexure 3 of Form B in Schedule 2 and safety, sanitary and hygienic requirements provided in the Schedule 4 contained under dif`

## Decision

**Default decision: PRESERVE_EXISTING_CHUNKS.**

Zero empty chunks, zero duplicate chunk IDs and complete required payload keys are observed; the only redundancy is 645 normalized-content groups (likely repeated standard clauses), and 9626 chunks lack section metadata. Neither is a chunk-boundary defect: re-chunking the corpus is NOT recommended without retrieval evidence of material damage.

Severity legend: CRITICAL (data-defect, fix before enrichment), HIGH (missing provenance — enrichment must not invent it), MEDIUM (quality gap — enrichment target), LOW (informational).
