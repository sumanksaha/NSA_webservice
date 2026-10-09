path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

with open(path, encoding="utf-8") as f:
    lines = f.read().split("\n")

# Find the end of score_structure (look for "return EvalScore" right before end of file)
# Insert after the last function (score_structure)
insert_idx = len(lines) - 5

block = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Main evaluation entry point",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def evaluate_workflow_answer(",
    "    query: str,",
    "    answer: str,",
    "    gold_answer: str | None = None,",
    "    chunks: list[Any] | None = None,",
    "    cited_chunk_ids: list[str] | None = None,",
    ") -> WorkflowAnswerEvaluation:",
    '    """Evaluate a workflow answer across all dimensions."""',
    "    faithfulness = score_faithfulness(answer, chunks or [], query)",
    "    completeness = score_completeness(answer, gold_answer, chunks)",
    "    citation_quality = score_citation_quality(answer, chunks)",
    "    structure = score_structure(answer, query)",
    "",
    "    # Overall score (weighted average)",
    "    overall = (",
    "        faithfulness.score * 0.35",
    "        + completeness.score * 0.30",
    "        + citation_quality.score * 0.15",
    "        + structure.score * 0.20",
    "    )",
    "",
    "    # Form coverage (for benchmark tracking)",
    "    fa_forms = _extract_forms(answer)",
    "    if gold_answer:",
    "        gold_forms = _extract_forms(gold_answer)",
    "        form_coverage = len(fa_forms & gold_forms) / max(len(gold_forms), 1)",
    "    else:",
    "        form_coverage = 0.0",
    "",
    "    return WorkflowAnswerEvaluation(",
    "        query=query,",
    "        answer=answer,",
    "        gold_answer=gold_answer,",
    "        retrieved_chunks=chunks,",
    "        cited_chunk_ids=cited_chunk_ids or [],",
    "        faithfulness=faithfulness,",
    "        completeness=completeness,",
    "        citation_quality=citation_quality,",
    "        structure=structure,",
    '        overall=EvalScore(name="overall", score=round(overall, 4), explanation="Weighted average of all dimensions.", detail={}),',
    "        fa_form_coverage=round(len(fa_forms), 4),",
    "        gold_form_coverage=round(form_coverage, 4),",
    '        steps_count=structure.detail.get("steps_found", 0),',
    "    )",
    "",
    "",
]

lines[insert_idx:insert_idx] = block

with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("Done")
