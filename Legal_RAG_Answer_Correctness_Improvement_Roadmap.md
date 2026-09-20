# Legal RAG — Answer Correctness Improvement Roadmap

**Purpose:** Engineering/research specification for improving final legal-answer correctness from the current ~36–38% range toward materially higher accuracy.

**Status:** Draft roadmap based on Experiments A, B and the partial results of Experiment C.

---

## 1. Executive Summary

The legal RAG has substantially improved retrieval and reranking, but final answer correctness remains around **36–38%**.

Current evidence:

- CE v2 reaches approximately **R@100 = 88%** under the current gold definition.
- Answer correctness at CE K=100 is approximately **38.4%**.
- Experiment C:
  - O1 Gold: ~36%
  - O2 Gold + Neighbors: ~37%
  - O3 Full Support: ~37%
- Therefore, increasing evidence availability and context size does **not** produce a proportional increase in final answer correctness.

### Core hypothesis

The remaining gap is increasingly likely to be in the transition:

> **retrieved legal evidence → legal interpretation → legal reasoning → final answer**

rather than being solely a retrieval problem.

This is a hypothesis to test, not a proven statement that the LLM itself is intrinsically incapable of legal reasoning.

### Strategic direction

Keep the current retrieval stack, but add:

1. Query decomposition.
2. Legal-unit-aware evidence representation.
3. Evidence/context selection.
4. Explicit structured legal reasoning.
5. Definition and cross-reference resolution.
6. Exception/proviso detection.
7. Fact-to-condition mapping.
8. Legal Auditor.
9. Citation verifier.
10. Final answer verifier.
11. Controlled self-correction.
12. Human benchmark/metric validation.

---

# 2. Experimental Evidence

## Experiment A — Reranker / Retrieval

Key finding:

- CE v2 substantially improved ranking.
- Recomputed R@100 ≈ **88%**.
- Gold evidence is frequently available in the candidate pool.
- A previous diagnosis that CE reranking alone represented ~78% of end-to-end failures was corrected after identifying a gold-unit/UUID matching problem.

### Implication

Do not treat the CE reranker as the sole explanation for low final answer correctness.

CE remains important because some gold evidence is still below the final context boundary, but retrieval is no longer the only layer requiring attention.

---

## Experiment B — CE Top-K

Approximate observations:

| K | Answer correctness | Retrieval recall |
|---:|---:|---:|
| 1 | 32.6% | ~28.7% |
| 10 | ~35.5% | ~54.7% |
| 20 | ~36.3% | ~64.7% |
| 50 | ~37% | ~76–77% |
| 100 | **38.4%** | **88.0%** |

The important relationship is:

> Retrieval availability increases dramatically, but answer correctness increases only modestly.

This suggests a retrieval-to-answer conversion bottleneck.

---

## Experiment C — Oracle Context

Partial results:

| Condition | Description | Correctness |
|---|---|---:|
| O1 | Gold evidence | ~0.36 |
| O2 | Gold + neighbors | ~0.37 |
| O3 | Full supporting evidence | ~0.37 |

Additional observations:

- O3 groundedness ≈ **0.85**.
- O1 groundedness ≈ **0.79**.
- O2 groundedness ≈ **0.78**.
- Citation recall remains around ~0.69–0.72.
- Citation precision remains around ~0.68–0.78.
- O3 uses substantially more context than O1, but correctness barely changes.

### Interpretation

More legal evidence does not automatically create a correct legal conclusion.

The likely research target is therefore **evidence utilization and legal reasoning**, not simply context quantity.

---

# 3. Important Evaluation Caveat

The current answer-correctness metric has been described as token-overlap/Jaccard-style correctness.

Do **not** assume that this is identical to legal correctness.

A legally correct answer may differ substantially in wording from the reference answer.

Conversely, an answer may have lexical overlap while containing a legally important error.

## Required benchmark audit

Before optimizing aggressively toward 88%, manually inspect approximately **30–50 answers currently marked incorrect**.

Classify each as:

- Clearly legally wrong.
- Partially correct.
- Legally acceptable but lexically different.
- Correct conclusion but incomplete.
- Correct reasoning but wrong citation.
- Genuine citation/evidence error.
- Genuine reasoning error.
- Other evaluation mismatch.

If many "incorrect" answers are legally acceptable, the benchmark metric is underestimating capability.

If most are genuinely wrong, the reasoning diagnosis becomes substantially stronger.

---

# 4. Recommended Target Architecture

Current conceptual architecture:

```text
Question
  ↓
Retrieval
  ↓
RRF
  ↓
CE
  ↓
Context
  ↓
LLM
  ↓
Answer
```

Recommended architecture:

```text
Question
  ↓
Query Decomposer
  ↓
Subquestion Retrieval
  ↓
RRF + CE v2
  ↓
Legal-Unit Evidence Selector
  ↓
Definition / Exception / Cross-reference Resolver
  ↓
Structured Legal Reasoner
  ↓
Legal Auditor
  ↓
Controlled Revision
  ↓
Answer Generator
  ↓
Citation Verifier
  ↓
Final Answer Verifier
  ↓
Final Answer
```

The key change is:

> **Do not force the model to jump directly from a large context to the final legal answer.**

---

# 5. Workstream A — Query Decomposition

Many legal questions contain multiple hidden reasoning steps.

Example:

```text
Question
  ↓
What entity/activity is involved?
  ↓
What provision governs it?
  ↓
What definitions apply?
  ↓
What conditions apply?
  ↓
Are there exceptions?
  ↓
Are cross-referenced provisions required?
  ↓
Do the stated facts satisfy the conditions?
  ↓
What is the legal consequence?
```

## Requirements

The decomposer should:

- Identify the legal issue.
- Identify the relevant entity/activity.
- Identify the governing legal provision.
- Identify definitions.
- Identify conditions/thresholds.
- Identify exceptions/provisos.
- Identify cross-references.
- Identify factual conditions that must be matched.

The decomposer must **not invent subquestions** merely to increase the number of retrieval calls.

---

# 6. Workstream B — Legal-Unit Evidence Representation

The system currently contains multiple chunks/payloads representing the same legal unit.

Experiment A exposed an important problem:

> Different payload UUIDs can represent the same gold legal unit.

Therefore, reasoning should operate on **legal units**, not only payload UUIDs.

## Recommended legal-unit representation

Each evidence item should preserve:

```text
act
regulation
section
subsection
clause
legal_unit_id
provision_type
text
parent_unit
child_units
cross_references
definitions
exceptions
source_metadata
retrieval_score
ce_score
```

## Provision types

At minimum:

- definition
- obligation
- prohibition
- permission
- exception
- proviso
- condition
- procedure
- authority
- penalty/consequence
- scope
- exemption

---

# 7. Workstream C — Evidence / Context Selection

Do not equate:

> CE top-K = final LLM context.

Use deep CE candidates as an evidence reservoir, then construct a compact, legally coherent context.

## Recommended flow

```text
CE top-50/100
      ↓
Legal-unit grouping
      ↓
Definition expansion
      ↓
Exception expansion
      ↓
Cross-reference expansion
      ↓
Duplicate removal
      ↓
Evidence importance scoring
      ↓
Compact coherent context
```

The selector should optimize for **legal completeness**, not only semantic similarity.

### Important

A smaller coherent context may be better than a large collection of semantically similar chunks.

---

# 8. Workstream D — Structured Legal Reasoner

Instead of asking:

> "Answer the question using the context."

require an intermediate structured reasoning object.

Recommended fields:

```text
issue
applicable_provisions
definitions
legal_rules
conditions
exceptions
facts
fact_condition_mapping
cross_references
conflicts_or_hierarchy
derived_conclusion
supporting_evidence
uncertainties
```

## Example structure

```text
ISSUE
What legal requirement applies?

APPLICABLE PROVISION
Section X / Regulation Y

DEFINITION
Term Z means ...

RULE
Requirement applies when A and B are satisfied.

EXCEPTION
Requirement does not apply when C.

FACTS
Question establishes A and B.
C is not established.

FACT-CONDITION MAPPING
A → satisfied
B → satisfied
C → not triggered

CONCLUSION
Requirement applies.

EVIDENCE
Section X(...)
Regulation Y(...)
```

The final answer should be generated from this representation.

---

# 9. Workstream E — Fact-to-Condition Mapping

A common legal reasoning error is:

> correct provision + incorrect application.

Therefore the reasoner must explicitly map facts to legal conditions.

For every material condition:

```text
Condition → Fact → Satisfied / Not satisfied / Unknown
```

Example:

```text
Condition A → Fact 1 → satisfied
Condition B → Fact 2 → satisfied
Condition C → no evidence → unknown
```

If a required condition is unknown, the answer should not silently assume it is satisfied.

---

# 10. Workstream F — Exception and Proviso Detection

Legal rules often contain:

- exceptions
- provisos
- exclusions
- "subject to" clauses
- "unless"
- "except"
- "notwithstanding"
- conditional requirements

The system should explicitly identify these structures.

## Required reasoning step

```text
Base rule
   ↓
Conditions
   ↓
Exceptions/provisos
   ↓
Determine whether exception applies
   ↓
Final rule
```

The Auditor must specifically check whether the model applied a general rule while overlooking an exception.

---

# 11. Workstream G — Definition Resolution

Before applying a provision:

1. Identify defined terms.
2. Retrieve the authoritative definition.
3. Determine whether the question's entity/activity satisfies that definition.
4. Only then apply the downstream rule.

This is particularly important for legal questions where ordinary-language meaning differs from a statutory/regulatory definition.

---

# 12. Workstream H — Cross-Reference Resolution

Many legal provisions depend on other provisions.

The system should support chains such as:

```text
Section A
  ↓ refers to
Section B
  ↓ defines
Term C
  ↓ modifies
Condition D
  ↓ produces
Conclusion
```

Only legally relevant cross-references should be followed.

Avoid indiscriminate graph expansion.

---

# 13. Workstream I — Multi-Hop Legal Reasoning

Explicitly support patterns such as:

### Pattern 1

```text
Definition
→ Scope
→ Governing provision
→ Conditions
→ Exception
→ Conclusion
```

### Pattern 2

```text
Primary provision
→ Cross-reference
→ Referenced provision
→ Application
```

### Pattern 3

```text
General rule
→ Specific rule
→ Exception
→ Final applicability
```

### Pattern 4

```text
Authority
→ Procedure
→ Compliance requirement
→ Consequence
```

Create a separate multi-hop benchmark subset to measure this.

---

# 14. Workstream J — Legal Auditor Agent

The Auditor should behave as a rigorous legal reviewer.

## Required checks

- Correct governing provision?
- Correct Act/Regulation?
- Correct section?
- Correct entity/activity?
- Correct definitions?
- All mandatory conditions considered?
- Exceptions/provisos considered?
- Cross-references considered?
- General vs specific rule handled correctly?
- Prohibition/obligation/permission distinguished?
- Conclusion entailed by evidence?
- Material claims supported?
- Citations attached to the correct claims?
- Unsupported certainty?
- Missing qualification?
- Incomplete answer?

## Auditor output

Do not return only:

```text
confidence = 0.82
```

Return structured defects:

```text
status: FAIL

defects:
  - missed_exception
  - unsupported_application

evidence:
  - Section X
  - Section Y

correction:
  ...
```

---

# 15. Workstream K — Answer Generator

The Answer Generator should receive:

1. Original question.
2. Structured legal reasoning.
3. Audited corrections.
4. Supporting evidence/citation map.

It should **not independently reconstruct the entire legal reasoning from a noisy 100-chunk context**.

This reduces the amount of hidden reasoning required at the final generation stage.

---

# 16. Workstream L — Citation Verifier

Citation verification should occur after answer generation.

For each material claim:

```text
Claim
  ↓
Supporting citation
  ↓
Does citation actually entail/support claim?
```

Check:

- citation recall
- citation precision
- citation-to-claim alignment
- primary-source preference
- unsupported claims

---

# 17. Workstream M — Final Answer Verifier

The final verifier should check:

### Legal correctness

Does the conclusion follow from the structured reasoning?

### Evidence support

Does the answer rely only on supported evidence?

### Completeness

Are important conditions/exceptions omitted?

### Scope

Does the answer answer the actual question?

### Citation

Do citations support the claims?

### Uncertainty

Is the answer appropriately qualified where evidence is incomplete?

---

# 18. Workstream N — Controlled Self-Correction

Avoid unrestricted recursive agent loops.

Use:

```text
Reason
  ↓
Audit
  ↓
If no critical defect → answer
  ↓
If defect → revise affected component
  ↓
Re-audit
  ↓
Maximum 1–2 correction cycles
```

Only trigger another LLM call when a concrete defect has been detected.

This controls cost and reduces reasoning drift.

---

# 19. Workstream O — Knowledge Graph Usage

The existing KG should support **reasoning**, not merely compete with dense retrieval.

Useful relations:

```text
provision → defines → term
provision → refers_to → provision
rule → has_condition → condition
rule → has_exception → exception
section → has_subsection → subsection
rule → has_consequence → consequence
```

The KG should help construct the legal reasoning chain and evidence set.

Measure KG value using **answer correctness and reasoning accuracy**, not only retrieval recall.

---

# 20. Experiment Roadmap

| Phase | Experiment | Purpose |
|---|---|---|
| Completed | A — Reranker | Locate retrieval/reranking losses |
| Completed | B — Top-K | Test context-depth effect |
| In progress | C — Oracle | Test context completeness |
| Next | Human error audit | Validate metric and classify errors |
| Next | Direct vs structured reasoning | Measure explicit reasoning contribution |
| Next | Reasoning + Auditor | Measure error detection/correction |
| Next | Evidence selector | Test coherent context assembly |
| Later | Multi-hop benchmark | Measure complex legal reasoning |
| Later | Adaptive K | Allocate evidence dynamically |
| Later | KG-assisted reasoning | Test graph-supported legal chains |

---

# 21. Most Important Next Experiment

## Experiment D — Direct Generation vs Structured Legal Reasoning

Use exactly the same questions and evidence.

### Condition A — Baseline

```text
Question + evidence
→ final answer
```

### Condition B — Structured reasoning

```text
Question + evidence
→ issue
→ provisions
→ conditions
→ exceptions
→ fact mapping
→ conclusion
→ final answer
```

### Condition C — Structured reasoning + Auditor

```text
Question + evidence
→ structured reasoning
→ Auditor
→ correction if required
→ final answer
```

Keep constant:

- benchmark
- retrieved evidence
- underlying model
- temperature/configuration
- answer evaluation
- citation requirements

Only change the reasoning architecture.

This isolates the contribution of explicit reasoning.

---

# 22. Error Taxonomy

Every failed question should eventually be assigned one or more categories:

| Error | Diagnostic | Intervention |
|---|---|---|
| Retrieval | Evidence unavailable | Retrieval/CE |
| Context assembly | Evidence available but omitted | Evidence selector |
| Extraction | Rule present but not recognized | Reasoning representation |
| Interpretation | Rule misunderstood | Legal reasoner |
| Application | Rule applied incorrectly | Fact-condition mapping |
| Exception | Proviso/exclusion missed | Exception detector |
| Definition | Defined term mishandled | Definition resolver |
| Multi-hop | Multiple provisions not combined | Decomposition + graph |
| Conflict | Competing provisions mishandled | Hierarchy resolver |
| Completeness | Answer incomplete | Auditor |
| Citation | Wrong/unsupported citation | Citation verifier |
| Evaluation | Metric mismatch | Benchmark audit |

---

# 23. Metrics Dashboard

Do not optimize only token overlap.

Track:

- Legal answer correctness.
- Coverage/completeness.
- Token overlap/Jaccard.
- Citation recall.
- Citation precision.
- Groundedness.
- Abstention quality.
- Per-question correctness.
- Error-category distribution.
- Latency.
- Context tokens.
- LLM call count.
- Cost per question.
- Correction success rate.
- Correction-induced regression rate.

---

# 24. Milestones

These are empirical milestones, not guarantees.

| Milestone | Target |
|---|---:|
| M0 | ~38% |
| M1 | 50%+ |
| M2 | 65%+ |
| M3 | 75%+ |
| M4 | 85%+ |
| M5 | ~88% |

The purpose of the milestones is to determine whether each architectural intervention produces a measurable gain.

---

# 25. What NOT to Do Yet

Do not:

- Train CE v3 solely because R@100 is 88%.
- Increase K to 200/500 merely to chase answer accuracy.
- Pass increasingly large contexts without evidence selection.
- Build an unconstrained multi-agent loop.
- Optimize only token overlap.
- Assume every incorrect answer is an LLM reasoning failure.
- Treat O3 ≈37% as a universal LLM ceiling.
- Use benchmark gold labels in production prompts.
- Spend large LLM budgets before identifying the dominant error classes.

---

# 26. Cost-Control Strategy

LLM calls should be used strategically.

## Cache deterministic stages

Cache:

- retrieval results
- RRF results
- CE scores
- legal-unit grouping
- evidence selection
- benchmark preprocessing

Do not rerun these for every reasoning experiment.

## Use staged evaluation

1. Small diagnostic subset.
2. Validate direction.
3. Full 150-question benchmark.
4. Error analysis.
5. Next intervention.

## Avoid unnecessary agent calls

The Auditor and Verifier should run conditionally where possible.

Example:

```text
Reasoning
  ↓
Low-risk / internally consistent
  → final answer

Potential defect
  ↓
Auditor
  ↓
Correction
```

Every experiment should record:

```text
question_id
llm_calls
input_tokens
output_tokens
latency
condition
answer
correctness
citations
audit_result
revision_count
```

---

# 27. Production-Oriented Recommended Pipeline

```text
                         USER QUESTION
                              |
                              v
                    +-------------------+
                    | Query Decomposer  |
                    +---------+---------+
                              |
                +-------------+-------------+
                |             |             |
                v             v             v
             Sub-Q1        Sub-Q2        Sub-Q3
                |             |             |
                +-------------+-------------+
                              |
                              v
                    Retrieval / RRF / CE
                              |
                              v
                    Legal Evidence Pool
                              |
                              v
                 Legal-Unit Evidence Selector
                              |
                              v
              +-----------------------------+
              | Definition / Exception /    |
              | Cross-reference Resolver    |
              +--------------+--------------+
                             |
                             v
                   Structured Legal Reasoner
                             |
                             v
                      Legal Argument
                             |
                             v
                      Legal Auditor
                             |
                    +--------+--------+
                    |                 |
                  PASS              FAIL
                    |                 |
                    |                 v
                    |          Controlled Revision
                    |                 |
                    +--------<---------+
                             |
                             v
                       Answer Generator
                             |
                             v
                      Citation Verifier
                             |
                             v
                     Final Answer Verifier
                             |
                             v
                        FINAL ANSWER
```

---

# 28. Immediate Action Plan

Execute in this order:

1. Finish Experiment C across the full 150-question benchmark.
2. Save complete per-question results.
3. Human-audit 30–50 answers marked incorrect at K=100/O3.
4. Build the error taxonomy.
5. Quantify how many failures are retrieval, reasoning, application, exception, multi-hop, citation, completeness, or evaluation failures.
6. Run Experiment D: Direct Generation vs Structured Legal Reasoning.
7. Add the failure-directed Legal Auditor.
8. Measure Auditor detection and correction rates.
9. Build legal-unit-aware context selection.
10. Add definition/exception/cross-reference resolution.
11. Add the final citation/answer verifier.
12. Only then decide whether CE v3 is a priority.

---

# 29. Research Hypothesis

The next-stage hypothesis is:

> **Once sufficient legal evidence is available, a structured legal-reasoning and verification architecture will improve answer correctness more than further increases in retrieval depth.**

Test this using paired experiments.

Do not assume the hypothesis is correct before measuring it.

---

# 30. Final Research Objective

The objective is not simply:

> "Increase R@100."

The objective is:

> **Convert available legal evidence into a correct, complete, appropriately qualified and correctly cited legal answer.**

The current system already demonstrates that substantial relevant evidence can be retrieved.

The next stage should therefore optimize:

```text
Evidence
   ↓
Understanding
   ↓
Legal reasoning
   ↓
Application
   ↓
Verification
   ↓
Correct answer
```

The desired end state is a legal RAG that can:

- find the governing law,
- identify the relevant legal unit,
- understand definitions,
- follow cross-references,
- identify conditions,
- detect exceptions,
- map facts to legal conditions,
- construct a defensible legal conclusion,
- cite the supporting provisions,
- recognize uncertainty,
- audit its own conclusion,
- and revise only when a concrete defect is detected.

---

# 31. Bottom Line

The current ~38% answer correctness should not be treated as the final capability of the project.

However, the experiments strongly indicate that **retrieval depth alone is unlikely to close the gap toward the current 88% evidence-recall level**.

The highest-value next direction is:

> **structured legal reasoning + legal-unit-aware evidence selection + exception/definition/cross-reference resolution + auditor + verifier + controlled revision**

The first decisive experiment should be:

> **Direct Generation vs Structured Legal Reasoning vs Structured Reasoning + Auditor, using exactly the same evidence.**

If this produces a substantial improvement, the architecture should be expanded. If it does not, the per-question failure taxonomy should determine the next intervention.

Do not assume an 88% final-answer ceiling. Establish the achievable ceiling empirically.

---

# 32. Concrete Engineering Implementation Blueprint & Codebase Mapping

This section defines the precise architectural contracts, data models, and codebase wiring to implement the features outlined in Sections 5 through 19 across the `app/rag/` subsystem.

## 32.1 Codebase File & Module Architecture

| Component / Feature | Proposed / Existing Module | Primary Responsibilities |
|---|---|---|
| **Query Decomposer** | `app/rag/planning/query_planner.py` & `app/rag/retrieval/subquery_decomposer.py` | Decomposes complex legal queries into legal sub-goals (entity, governing provisions, definitions, exceptions, cross-references). |
| **Legal-Unit Identity & Evidence Selector** | `app/rag/retrieval/legal_identity.py` & `app/rag/retrieval/evidence_selector.py` | Canonicalizes legal units (`ACT::SEC::SUBSEC::CLAUSE`), groups CE top-50/100 pool, expands definitions/exceptions, and filters redundant chunks. |
| **Structured Legal Reasoner (IRAC/FIRAC)** | `app/rag/generation/structured_reasoner.py` *(New)* | Generates an intermediate structured legal reasoning object before answer generation. |
| **Fact-to-Condition Mapper** | `app/rag/generation/structured_reasoner.py` *(New)* | Evaluates conditions against facts (`satisfied`, `not_satisfied`, `unknown`). |
| **Exception & Definition Resolver** | `app/rag/retrieval/reference_graph.py` & `app/rag/planning/kg_reasoner.py` | Discovers provisos, exclusions, and statutory definitions using rule-based filters and the Knowledge Graph. |
| **Legal Auditor Agent** | `app/rag/agent/nodes/auditor.py` *(New)* | Critiques intermediate legal arguments and emits structured defects. |
| **Controlled Revision Loop** | `app/rag/agent/graph.py` & `app/rag/agent/state.py` | LangGraph conditional edge routing failed audits back to the reasoner (max 1–2 cycles). |
| **Answer Generator** | `app/rag/generation/grounded_service.py` | Renders natural language prose and statutory citations from the audited structured argument. |
| **Citation & Answer Verifiers** | `app/rag/verification/claim_extractor.py` & `app/rag/verification/citation_validator.py` | Verifies claim-level entailment and overall legal consistency. |

---

## 32.2 Data Contracts & Schemas

### 1. Structured Legal Argument Schema (`StructuredLegalArgument`)
```python
from typing import Any, Literal
from pydantic import BaseModel, Field

class ConditionEvaluation(BaseModel):
    condition_id: str
    condition_text: str
    fact_reference: str = Field(description="Facts from question/context bearing on this condition")
    status: Literal["satisfied", "not_satisfied", "unknown"]
    explanation: str

class StructuredLegalArgument(BaseModel):
    issue: str = Field(description="The primary legal issue or question to be determined")
    applicable_provisions: list[str] = Field(description="Canonical legal unit IDs e.g. FSS_ACT::31::1")
    definitions_applied: dict[str, str] = Field(default_factory=dict, description="Statutory definitions applied")
    legal_rules: list[str] = Field(description="Base statutory/regulatory obligations or prohibitions")
    exceptions_considered: list[dict[str, Any]] = Field(default_factory=list, description="Provisos or exceptions analyzed")
    condition_evaluations: list[ConditionEvaluation] = Field(description="Explicit fact-to-condition mappings")
    cross_references_followed: list[str] = Field(default_factory=list)
    conflicts_or_hierarchy: list[str] = Field(default_factory=list)
    derived_conclusion: str = Field(description="The legal determination strictly derived from the conditions")
    supporting_citations: list[str] = Field(description="Exact canonical legal units supporting the conclusion")
    uncertainties: list[str] = Field(default_factory=list, description="Qualifications when conditions are unknown")
```

### 2. Legal Auditor Defect Schema (`AuditResult`)
```python
class AuditDefect(BaseModel):
    defect_type: Literal[
        "missed_exception",
        "unsupported_application",
        "definition_mismatch",
        "invalid_citation",
        "incomplete_scope",
        "unsupported_certainty"
    ]
    severity: Literal["critical", "minor"]
    provision_reference: str | None = None
    explanation: str
    required_correction: str

class AuditResult(BaseModel):
    status: Literal["PASS", "FAIL"]
    defects: list[AuditDefect] = Field(default_factory=list)
    revised_argument: StructuredLegalArgument | None = None
```

---

## 32.3 LangGraph State & Graph Integration

### State Additions (`app/rag/agent/state.py`)
```python
# Additions to RAGState TypedDict:
structured_argument: dict[str, Any] | None
audit_result: dict[str, Any] | None
revision_count: int
max_revisions: int
legal_unit_evidence: list[dict[str, Any]]
```

### Routing Logic (`app/rag/agent/graph.py`)
```python
def route_after_audit(state: RAGState) -> str:
    """Conditional edge after the legal auditor evaluates the reasoning."""
    audit = state.get("audit_result") or {}
    status = audit.get("status", "PASS")
    revision_count = int(state.get("revision_count", 0))
    max_revisions = int(state.get("max_revisions", 2))
    
    if status == "FAIL" and revision_count < max_revisions:
        return "revise_reasoning"
    return "generate_answer"
```

---

## 32.4 Phased Implementation Schedule

1. **Phase 1: Experiment D & Failure Taxonomy**
   - Conduct a human audit of 30–50 failing answers from Experiment C.
   - Run **Experiment D**:
     - *Condition A:* Direct baseline generation.
     - *Condition B:* Structured reasoning (`StructuredLegalArgument` -> Answer).
     - *Condition C:* Structured reasoning + Legal Auditor + 1-cycle revision.
   - Measure correctness gain vs. latency and token overhead.

2. **Phase 2: Evidence Selector & Legal-Unit Canonicalization**
   - Enhance `legal_identity.py` and `evidence_selector.py` to group chunks by legal unit and prune duplicates.
   - Integrate definition and exception expansion using rule patterns and `kg_reasoner.py`.

3. **Phase 3: Production Graph Integration**
   - Hook `structured_reasoner_node` and `auditor_node` into `app/rag/agent/graph.py` gated by `ENABLE_STRUCTURED_REASONER` and `ENABLE_LEGAL_AUDITOR` flags.
   - Run end-to-end regression evaluation across the full 150-question benchmark.

