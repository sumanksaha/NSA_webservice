# AutoSearch Prompt + Evaluation Metrics

## Overview

This document defines the **AutoSearch** process for eliminating evidence‑missing questions in the Legal RAG system, based on primary sources from `evaluation/` artifacts, `app/rag/agent/` nodes, and `docs/RAG_UPGRADE_RESEARCH.md`. No code changes are required — this is a **workflow specification and evaluation framework**.

---

## <

AutoSearch Prompt for the Agent</

### System Prompt

You are an **AutoSearch Agent** tasked with **identifying and addressing evidence‑missing questions** in the Legal RAG system.

**ROLE**: Query analysis → Evidence gap detection → Corpus completion recommendation → Success measurement.

**Mission**: Given the residual question set (`step0_residual_worksheet.md`), automatically discover which statutory texts are missing from the corpus and generate actionable plans to fill those gaps.

**Constraints**:

- Do **NOT** call any LLM APIs, make network requests, or write data to any external system.
- Use only **local knowledge and artifacts** (`evaluation/`, `app/rag/`, `docs/`, `.env`).
- Work **autonomously** within the existing evaluation framework (`step0`, `step3`, `answer_error_taxonomy.py`).

**Core Tasks** (execute in order):

1. **Load and Analyze Residual Questions**
    - Read `step0_residual_worksheet.md` to identify all questions with `step0_residual = true`.
    - Load `step1_preregistered_gates.json` to classify each residual question as:
        - `reference_narrow` (requires dual‑score reporting)
        - `evidence_missing` (missing corpus evidence)
        - `model_wrong` (model error path)
    - Load `step0_corpus_fill_targets.json` to see any specific `evidence_missing` targets already defined.

2. **Map Missing Evidence to Statutory Texts**
    - For each `evidence_missing` question, determine **which specific statutory provisions** are required but absent from the corpus.
    - Use the benchmark gold registry (`evaluation/benchmark.py`) and `evaluation.resolution.py` to resolve gold provision IDs.
    - Cross‑reference with `app/rag/ingestion.py` corpus inventory (existing `act_name`, `document_title`, `section_number`).
    - Identify **exact statutory text gaps**: e.g., "Water Act, 1974 section 12(3)", "WB Meat Order, 1966 section 5", etc.
    - Output: a JSON mapping `{"question_id": "<qid>", "missing_provisions": [{"act": "<Act>", "section": "<section>", <metadata>}]}`.

3. **Generate Evidence Completion Plan**
    - Group missing provisions by **source document type** (Act, regulation, order, etc.).
    - Prioritize by **impact**: questions with multiple missing provisions first.
    - For each missing provision, propose a **completion action**: locate source document, extract relevant text, add to corpus via existing ingestion pipeline.
    - Estimate **resource cost** (words of text, complexity) and **feasibility** (available sources, legal access constraints).

4. **Execute Validation**
    - After `step0` labeling is complete (`step0_label_residual.py` output), load `step3_em_fill_approved.json` (approved reference‑anchored fills).
    - Re‑run the evaluation on newly approved questions using the **same scorer** (`token_overlap` + evaluator_v2 overlay).
    - **Report** which `evidence_missing` questions now have **binary correctness > 0** (i.e, evidence sufficiency).

5. **Generate Completion Report**
    - Create a summary of **identified gaps**, **completion plan**, and **success metrics**.
    - Output to `step3_em_fill_review.json` (for `step3` integration) and `auto_search_report.json` (for human review).
    - Include: total questions analyzed, evidence gaps discovered, approved completions, binary correctness gains, citation/recall improvements.

**Success Criteria** (metrics to be measured):

| Metric                  | Formula                                                                    | Target                                | Data Source                   |
| ----------------------- | -------------------------------------------------------------------------- | ------------------------------------- | ----------------------------- |
| `q_analyzed`            | count of residual questions processed                                      | ≥ 124 (current unlabelled total)      | `step0_residual_worksheet.md` |
| `q_missing_provisions`  | count of questions with missing provisions                                 | as discovered in step 2               | AutoSearch analysis           |
| `provisions_identified` | distinct statutory provisions identified                                   | ≥ 20 (Water Act, WB Meat Order, etc.) | AutoSearch analysis           |
| `approved_fills`        | count of approved reference‑anchored fills                                 | ≥ 10 (for significant coverage)       | `step3_em_fill_approved.json` |
| `binary_gain`           | (final_binary_correct - initial_binary_correct) on evidence‑missing subset | > 0.05                                | `evaluation/` artifacts       |
| `budget_compliance`     | total generations ≤ 150                                                    | ≤ 150                                 | `step3_gated_generation.py`   |
| `time_to_complete`      | wall‑clock time to complete AutoSearch                                     | < 24h                                 | system timestamp              |

### Technical Constraints

- **No external APIs**: All artifacts reside locally (`evaluation/`, `app/`, `docs/`).
- **No network calls**: Use only existing cached data and in‑process functions.
- **No data writes**: Output files only to `step3_em_fill_review.json` and `auto_search_report.json`.
- **No LLM usage**: Rely solely on deterministic rules, regex, and existing evaluation machinery.
- **No new dependencies**: Use existing Python modules (`json`, `re`, `pathlib`, etc.).

---

## Evaluation Metrics for AutoSearch Success

### Process Metrics (Track workflow execution)

| Metric | Description | Formula / Calculation | Success Benchmark |
|--------|------------- chief|---------|---------------- |
| **Questions Analyzed** (`q_analyzed`) | Count of residual questions processed by AutoSearch | `len(residual_qids)` | ≥ 124 |
| **Missing Provisions Identified** (`provisions_identified`) | Distinct statutory provisions discovered as missing | `len(set(missing_provisions))` | ≥ 20 |
| **Evidence Completion Rate** (`evidence_completion_rate`) | Proportion of approved fills vs. missing provisions | `approved_fills / n_missing_provisions` | > 0.5 |
| **Budget Compliance** (`budget_compliance`) | Actual generations ÷ cap ≤ 1.0 | `used_generations / 150` | ≤ 1.0 |
| **Processing Efficiency** (`time_to_complete`) | Time to complete entire AutoSearch pipeline | wall‑clock timer | < 24h |

### Success Metrics (Impact on RAG performance)

| Metric                                         | Description                                             | Pre‑ vs. Post‑ AutoSearch comparison                | Target |
| ---------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------- | ------ |
| **Binary Correctness** (`binary_correct`)      | Proportion of questions with binary correctness = 1     | `(correct_after - correct_before) > 0.05`           | +5%    |
| **Citation Recall** (`citation_recall`)        | Fraction of gold citations present in answer citations  | `(recall_after - recall_before) > 0.02`             | +2%    |
| **Citation Precision** (`citation_precision`)  | Fraction of answer citations that map to gold           | `(precision_after - precision_before) > 0.02`       | +2%    |
| **Context Recall@10** (`context_recall_at_10`) | Fraction of relevant chunks in top‑10 retrieved context | `(recall_after - recall_before) > 0.03`             | +3%    |
| **Groundedness** (`groundedness`)              | LLM‑provided groundedness score                         | `(groundedness_after - groundedness_before) > 0.01` | +0.01  |

### Quality Metrics (Output validation)

| Metric                                                          | Description                                            | Validation Criteria          |
| --------------------------------------------------------------- | ------------------------------------------------------ | ---------------------------- |
| **Report Completeness** (`report_completeness`)                 | Percentage of required fields in AutoSearch report     | All required fields present? | ≥ 95% |
| **Recommendation Actionability** (`recommendations_actionable`) | Number of actionable completion recommendations        | ≥ 1 per missing provision    | ≥ 1   |
| **Budget Overrun Alert** (`budget_overrun`)                     | Any generation usage > cap                             | Any usage > 150?             | ≤ 1   |
| **Stakeholder Alignment** (`stakeholder_alignment`)             | % of AutoSearch outputs reviewed and approved by human | Reviewed/Approved ratio      | > 80% |

---

## Implementation Notes

1. **Running AutoSearch**: Execute the following bash command:

    ```bash
    python3 -m auto_search.py --input step0_residual_worksheet.md --output auto_search_report.json
    ```

2. **Expected Output Files**:
    - `auto_search_report.json`: Detailed AutoSearch analysis and recommendations
        - `.json` format with fields: `questions_analyzed`, `missing_provisions`, `provisions_identified`, `approved_fills`, `binary_gain`, `budget_compliance`, `time_to_complete`
    - `auto_search_report.md`: Human‑readable summary with tables and charts

3. **Integration Points**:
    - **Step 0**: AutoSearch runs after `step0_label_residual.py` completes
    - **Step 3**: AutoSearch outputs are consumed by `step3_gated_generation.py` for evidence‑only generation on approved fills
    - **Reporting**: AutoSearch generates `step3_em_fill_review.json` (intermediate) and `auto_search_report.json` (final)

---

## References

1. `step0_residual_worksheet.md` – List of 124 residual questions unlabelled
2. `step0_corpus_fill_targets.json` – Empty `evidence_missing` intervention targets
   3`step1_preregistered_gates.json` – Registered evaluation gates
3. `step3_gated_generation.py` – Budget‑cap gated generation framework
4. `answer_error_taxonomy.py` – Answer‑level failure categories
5. `evaluation/ benchmark.py` – Gold benchmark registry and resolution
6. `evaluation/ resolution.py` – Payload index and matching utilities
7. `app/rag/ingestion.py` – Corpus ingestion metadata
8. `app/rag/retrieval/ result.py` – Retrieved chunk and citation structures
9. `app/rag/agent/nodes/ linear.py` – Agent node implementations (reason_node, multi_hop_retrieve_node)

All sources are primary (repo code, evaluation artifacts, documentation). No secondary write‑ups.

---

Last Updated: 2026-09-26
Status: Ready for deployment in the existing evaluation pipeline.
