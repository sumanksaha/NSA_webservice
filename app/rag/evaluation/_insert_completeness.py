path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

with open(path, encoding="utf-8") as f:
    lines = f.read().split("\n")

# Find the function "def score_faithfulness" and insert after it
insert_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith("def score_faithfulness"):
        insert_idx = i + 1
        break

if insert_idx is None:
    print("ERROR: score_faithfulness not found")
else:
    block = [
        "",
        "# ---------------------------------------------------------------------------",
        "# Completeness",
        "# ---------------------------------------------------------------------------",
        "",
        "",
        "def _extract_forms(answer: str) -> set[str]:",
        '    """Extract form numbers mentioned in an answer."""',
        "    forms = set()",
        '    for m in _FORM_RE.finditer(answer or ""):',
        "        forms.add(m.group(2).lower())",
        "    return forms",
        "",
        "",
        "def _extract_steps(answer: str) -> list[str]:",
        '    """Extract numbered steps from an answer (1., 2., 3., etc.)."',
        "    patterns = [",
        '        r"^\\s*(\\d+)\\.\\s+(.*?)(?=^\\s*\\d+\\.\\s+|\\Z)",',
        '        r"^\\s*[*\\-]\\s+(.*?)(?=^\\s*[*\\-]\\s+|\\Z)",',
        "    ]",
        "    steps = []",
        "    for pattern in patterns:",
        '        for m in re.finditer(pattern, answer or "", re.MULTILINE | re.DOTALL):',
        "            steps.append(m.group(1).strip())",
        "    return steps",
        "",
        "",
        "def score_completeness(answer: str, gold_answer: str | None, chunks: list[Any] | None = None) -> EvalScore:",
        '    """Check if the answer covers all forms and steps from the gold answer."""',
        "    if not gold_answer:",
        '        return EvalScore(name="completeness", score=1.0, explanation="No gold answer; cannot check completeness.", detail={"note": "no_gold"})',
        "",
        "    fa_forms = _extract_forms(answer)",
        "    gold_forms = _extract_forms(gold_answer)",
        "",
        "    gold_steps = _extract_steps(gold_answer)",
        "    answer_steps = _extract_steps(answer)",
        "",
        "    steps_covered = 0",
        "    for gs in gold_steps:",
        "        for as_ in answer_steps:",
        "            if token_coverage(content_tokens(gs), content_tokens(as_)) >= 0.5:",
        "                steps_covered += 1",
        "                break",
        "",
        "    forms_covered = len(fa_forms & gold_forms) / max(len(gold_forms), 1)",
        "",
        "    form_score = forms_covered",
        "    step_score = steps_covered / max(len(gold_steps), 1) if gold_steps else 0.0",
        "",
        "    if chunks:",
        "        needles = content_tokens(gold_answer)",
        "        chunk_texts = [chunk_text(c) for c in chunks]",
        "        evidence_matched = any(token_coverage(needles, ct) >= 0.5 for ct in chunk_texts)",
        "    else:",
        "        evidence_matched = True",
        "",
        "    score = (form_score + step_score + float(evidence_matched)) / 3.0",
        "",
        "    detail = {",
        '        "fa_forms": sorted(fa_forms),',
        '        "gold_forms": sorted(gold_forms),',
        '        "gold_steps_matched": steps_covered,',
        '        "gold_steps_total": len(gold_steps),',
        '        "form_score": round(form_score, 4),',
        '        "step_score": round(step_score, 4),',
        '        "evidence_matched": evidence_matched,',
        "    }",
        '    explanation = f"forms={form_score:.2f}, steps={step_score:.2f}, evidence={evidence_matched} -> {score:.2f}"',
        "",
        '    return EvalScore(name="completeness", score=round(score, 4), explanation=explanation, detail=detail)',
        "",
        "",
    ]

    lines[insert_idx:insert_idx] = block

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Inserted completeness at index {insert_idx}")

print("Done")
