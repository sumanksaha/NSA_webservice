# Build workflow_comparator.py part 1

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_comparator.py"

parts = [
    '"""Workflow answer comparator -- compare user vs system workflow answer."""',
    "",
    "import re",
    "from dataclasses import dataclass",
    "from typing import Any",
    "",
    "__all__ = [",
    "    WorkflowAnswerComparison,",
    "    compare_workflow_answers,",
    "    find_missing_forms,",
    "    find_extra_forms,",
    "    find_missing_steps,",
    "    find_extra_steps,",
    "    find_citation_issues,",
    "    find_factual_discrepancies,",
    "    build_improvement_summary,",
    "]",
    "",
    '_FORM_RE = re.compile(r"\\b(form|forms?)\\s+(ii|iii|iv|v|vi|vii|viii)\\b", re.IGNORECASE)',
    '_NUMBER_RE = re.compile(r"\\b(\\d+)\\.\\s+(.*?)(?=\\s*\\d+\\.\\s+|\\Z)", re.MULTILINE | re.DOTALL)',
]

with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
