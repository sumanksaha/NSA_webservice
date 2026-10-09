# Build workflow_evaluator.py part 3

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Helper functions",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def _extract_forms(answer: str) -> set[str]:",
    "    forms = set()",
    '    for m in _FORM_RE.finditer(answer or ""):',
    "        forms.add(m.group(2).lower())",
    "    return forms",
    "",
    "",
    "def _extract_steps(answer: str) -> list[str]:",
    "    steps = []",
    "    patterns = [",
    '        r"\\s*(\\d+)\\.\\s+(.*?)(?=\\s*\\d+\\.\\s+|\\Z)",',
    '        r"\\s*[*\\-]\\s+(.*?)(?=^\\s*[*\\-]\\s+|\\Z)",',
    "    ]",
    "    for pattern in patterns:",
    '        for m in re.finditer(pattern, answer or "", re.MULTILINE | re.DOTALL):',
    "            steps.append(m.group(1).strip())",
    "    return steps",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
