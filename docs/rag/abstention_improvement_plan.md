# Improving Abstentions in Legal RAG - Analysis and Recommendations

## Executive Summary

This document analyzes abstention patterns in the legal RAG system and provides prioritized recommendations for improving accuracy. Based on evaluation data, the system has 89 model_wrong questions broken down into retrieval_rank (27), generation (59), never_retrieved (3), and abstention_gate (31).

## Current Abstention Breakdown

| Category | Count | Percentage | Description |
|----------|-------|------------|-------------|
| retrieval_rank | 27 | 30.3% | Evidence retrievable but not surfaced |
| generation | 59 | 66.3% | Model answered without proper evidence |
| never_retrieved | 3 | 3.4% | Gold evidence not in pool |
| abstention_gate total | 31 | 34.8% | System should have abstained |

Within abstention_gate: never_retrieved 1 (3.2%), generation 20 (64.5%), retrieval_rank 10 (32.3%)

## Key Metrics

- **abstention_discipline**: "abstains exactly when the reference expects it"
- **false_abstention_rate**: 0.0 (test cohort)
- **missed_abstention_rate**: 0.0 (test cohort)

## Recommendations

### 1. Reduce "retrieval_rank" (27 questions)
- Improve query-document ranking using per-query-type budgets (effective_context_windows: 8-12 slots)
- Hybrid search: vector + keyword matching for legal terminology
- Query type routing using C1/C7 taxonomy
- Increase top-k when pool contains relevant evidence

### 2. Reduce "generation" (59 questions)
- Stricter grounding enforcement before generation
- Evidence-first prompting: require verbatim quotes from context
- Citation requirements: answers without [n] markers route to abstention
- Strengthen C5 hallucination_guard

### 3. Address "never_retrieved" (3 questions)
- Expand retrieval pool or use iterative retrieval
- Improved chunking to prevent provisions being split incorrectly
- Synonym/alt-term expansion for legal vocabulary
- Cross-act reference coverage

### 4. Calibrate Abstention Triggers
- Tune threshold based on gold depth (median 8, p90 105), query type, confidence scores
- Maintain false_abstention_rate: 0.0 - don't match substantive answers
- Maintain missed_abstention_rate: 0.0 - catch all legitimate refusal patterns

### 5. Enhance Abstention Lexicon
- Monitor pattern coverage on failure cohort
- Add domain-specific patterns: "The Act does not specify...", "No provision establishes...", "Cannot be derived from..."
- Balance precision/recall using false_abstention_rate and missed_abstention_rate

### 6. Context Hygiene Improvements
- Use amendment-prose drop (freed ~25 slots on average)
- Maintain 91-95% gold_in_prompt rate across all question types
- Optimize window slot management per query type

### 7. Monitoring and Feedback Loop
- Track abstention_rate alongside groundedness_score using summarize_groundedness
- Quarterly lexicon review based on residual error analysis
- A/B test abstention thresholds on calibration split
- Correlate with question type for per-type improvements

## Priority Order

### Immediate (0-2 weeks)
- Tune abstention lexicon
- Ensure is_abstention() production logic matches evaluation rules

### Short-term (1 month)
- Improve prompt templating to require citations before generation
- Increase retrieval top-k for difficult questions

### Medium-term (2-3 months)
- Implement query-type-specific retrieval budgets
- Add domain-specific negation patterns
- Enhance context window management per query type

### Long-term (3+ months)
- Hybrid retrieval optimization (vector + keyword)
- Iterative retrieval with re-ranking
- End-to-end abstention vs. answer branching system

## Code Reference Points

- **Abstention lexicon**: `app/rag/generation/abstention.py` - `ABSTENT_MARKERS_RE`, `is_abstention()`
- **Evaluation rules**: `evaluation/abstention_rule.py` - `abstain_credit()`, `summarize_groundedness()`
- **Evaluator v4**: `evaluation/evaluator_v4.py` - C3 `abstention_discipline`
- **Prompt templates**: `generation/prompt_template.py` - strengthen grounding
- **Query type registry**: `app/rag/model_runtime.py`, `app/rag/query_type_registry.py` - per-type optimization
- **Context builder**: `generation/context_builder.py` - window slot management
- **Benchmarks**: `evaluation/out/ceiling_v5/` - failure attribution and rubric data