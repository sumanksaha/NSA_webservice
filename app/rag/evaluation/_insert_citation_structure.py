path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

with open(path, encoding="utf-8") as f:
    lines = f.read().split("\n")

# Find the function "def score_completeness" and insert after it
insert_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith("def score_completeness"):
        insert_idx = i + 1
        break

if insert_idx is None:
    print("ERROR: score_completeness not found")
else:
    block = [
        "",
        "# ---------------------------------------------------------------------------",
        "# Citation quality",
        "# ---------------------------------------------------------------------------",
        "",
        "",
        "def score_citation_quality(answer: str, chunks: list[Any] | None = None) -> EvalScore:",
        '    """Check that citations are correctly formatted and point to valid sources."""',
        '    cited = _CITATION_RE.findall(answer or "")',
        "    if not cited:",
        '        return EvalScore(name="citation_quality", score=0.5, explanation="No citations found; answer may be missing source references.", detail={"cited": [], "count": 0})',
        "",
        "    valid_numbers = set()",
        "    for c in cited:",
        "        try:",
        "            valid_numbers.add(int(c))",
        "        except ValueError:",
        "            pass",
        "",
        "    if not valid_numbers:",
        '        return EvalScore(name="citation_quality", score=0.0, explanation="Citations contain non-integer values.", detail={"cited": cited})',
        "",
        "    if chunks:",
        "        chunk_ids = set()",
        "        for c in chunks:",
        '            if hasattr(c, "chunk_id"):',
        "                chunk_ids.add(c.chunk_id)",
        "            elif isinstance(c, dict):",
        '                chunk_ids.add(str(c.get("chunk_id", "")))',
        "",
        "        if not chunk_ids:",
        '            return EvalScore(name="citation_quality", score=round(len(valid_numbers) / max(len(cited), 1), 4), explanation=f"Cited {len(valid_numbers)}/{len(cited)} markers.", detail={"cited": sorted(valid_numbers), "count": len(cited)})',
        "",
        "        valid_citations = [cid for cid in valid_numbers if str(cid) in chunk_ids]",
        "        score = len(valid_citations) / len(valid_numbers)",
        '        return EvalScore(name="citation_quality", score=round(score, 4), explanation=f"Cited {len(valid_citations)}/{len(valid_numbers)} markers point to valid chunks.", detail={"cited": sorted(valid_numbers), "valid": sorted(valid_citations)})',
        "",
        '    return EvalScore(name="citation_quality", score=round(len(valid_numbers) / max(len(cited), 1), 4), explanation=f"Cited {len(valid_numbers)}/{len(cited)} markers.", detail={"cited": sorted(valid_numbers)})',
        "",
        "",
        "# ---------------------------------------------------------------------------",
        "# Structure",
        "# ---------------------------------------------------------------------------",
        "",
        "",
        'def score_structure(answer: str, query: str = "") -> EvalScore:',
        '    """Check if the answer follows the expected procedure format."""',
        '    text = (answer or "").strip()',
        "    if not text:",
        '        return EvalScore(name="structure", score=0.0, explanation="Empty answer.", detail={"note": "empty"})',
        "",
        "    step_count = len(_extract_steps(text))",
        "    if step_count == 0:",
        '        if re.search(r"\\d+\\.", text):',
        "            step_count = 1",
        '        elif re.search(r"\\n", text):',
        "            step_count = 2",
        "        else:",
        "            step_count = 0",
        "",
        "    if step_count == 0:",
        "        score = 0.3",
        '        explanation = "No clear steps found; may be narrative answer."',
        "    elif step_count <= 3:",
        "        score = 0.7",
        '        explanation = f"{step_count} steps; acceptable for simple procedures."',
        "    elif step_count <= 7:",
        "        score = 0.85",
        '        explanation = f"{step_count} steps; good procedure coverage."',
        "    else:",
        "        score = 0.95",
        '        explanation = f"{step_count} steps; comprehensive coverage."',
        "",
        '    return EvalScore(name="structure", score=round(score, 4), explanation=explanation, detail={"steps_found": step_count})',
        "",
        "",
    ]

    lines[insert_idx:insert_idx] = block

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Inserted citation/structure at index {insert_idx}")

print("Done")
