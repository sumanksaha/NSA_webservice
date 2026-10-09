# Build workflow_evaluator.py part 6

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Citation quality",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def score_citation_quality(answer: str, chunks: list | None = None) -> EvalScore:",
    '    cited = _CITATION_RE.findall(answer or "")',
    "    if not cited:",
    '        return EvalScore(name="citation_quality", score=0.5, explanation="No citations found.", detail={"count": 0})',
    "    valid_numbers = set()",
    "    for c in cited:",
    "        try:",
    "            valid_numbers.add(int(c))",
    "        except ValueError:",
    "            pass",
    "    if not valid_numbers:",
    '        return EvalScore(name="citation_quality", score=0.0, explanation="Invalid citations.", detail={"cited": cited})',
    "    if chunks:",
    "        chunk_ids = set()",
    "        for c in chunks:",
    '            if hasattr(c, "chunk_id"):',
    "                chunk_ids.add(c.chunk_id)",
    "        valid = [cid for cid in valid_numbers if str(cid) in chunk_ids]",
    '        return EvalScore(name="citation_quality", score=round(len(valid) / len(valid_numbers), 4), explanation=f"Cited {len(valid)}/{len(valid_numbers)} valid.", detail={"cited": sorted(valid_numbers), "valid": sorted(valid)})',
    '    return EvalScore(name="citation_quality", score=round(len(valid_numbers) / max(len(cited), 1), 4), explanation=f"Cited {len(valid_numbers)}/{len(cited)}.", detail={"cited": sorted(valid_numbers)})',
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
