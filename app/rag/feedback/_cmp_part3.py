# Build workflow_comparator.py part 3

path = r"C:/github/NSA_webservice/app/rag/feedback/workflow_comparator.py"

parts = [
    "",
    "# ---------------------------------------------------------------------------",
    "# Form detection",
    "# ---------------------------------------------------------------------------",
    "",
    "",
    "def find_missing_forms(system_answer: str, user_answer: str) -> list[str]:",
    '    """Forms present in system answer but MISSING in user answer."""',
    '    sys_forms = set(_FORM_RE.findall(system_answer or ""))',
    '    user_forms = set(_FORM_RE.findall(user_answer or ""))',
    '    sys_nums = {m[1].lower() for m in sys_forms if m[0].lower() == "form"}',
    '    user_nums = {m[1].lower() for m in user_forms if m[0].lower() == "form"}',
    "    missing = sorted(sys_nums - user_nums)",
    "    return missing",
    "",
    "",
    "def find_extra_forms(system_answer: str, user_answer: str) -> list[str]:",
    '    """Forms present in user answer but NOT in system answer."""',
    '    sys_forms = set(_FORM_RE.findall(system_answer or ""))',
    '    user_forms = set(_FORM_RE.findall(user_answer or ""))',
    '    sys_nums = {m[1].lower() for m in sys_forms if m[0].lower() == "form"}',
    '    user_nums = {m[1].lower() for m in user_forms if m[0].lower() == "form"}',
    "    extra = sorted(user_nums - sys_nums)",
    "    return extra",
]

with open(path, "a", encoding="utf-8") as f:
    f.write("\n".join(parts))
