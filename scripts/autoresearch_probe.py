"""Autoresearch probe: form detector + procedure intent + md loader + chunker."""

import sys

sys.path.insert(0, "C:/github/NSA_webservice")
import os

os.chdir("C:/github/NSA_webservice")

print("== 1. form_references ==")
from app.rag.retrieval.form_references import detect_form, form_query

tests = [
    "Which form can the FBO use to appeal?",
    "What happens after the FSO takes a sample?",
    "When FSO seizes an item which forms?",
    "appeal to designated officer in Form VIII",
    "notice in Form V regulation 2.4.1.3",
    "How can the FBO appeal to the designated officer?",
]
for q in tests:
    print(f"  {q!r} -> form={detect_form(q)!r} fquery={form_query(q)!r}")

print("== 2. procedure intent (food_query_understanding public API) ==")
import app.rag.retrieval.food_query_understanding as _fqu

print("  public fns:", [n for n in dir(_fqu) if not n.startswith("_")][:20])

print("== 3. md loader ==")
from app.document_loader.loader import DocumentLoaderFactory

print("  .md supported:", DocumentLoaderFactory.is_supported("x.md"))
print("  exts has .md:", ".md" in DocumentLoaderFactory.supported_extensions())

print("== 4. chunker md heading ==")
from app.rag.chunker import _extract_markdown_section_title

for t in ["## Seizure", "## Sampling", "plain text"]:
    print(f"  {t!r} -> {_extract_markdown_section_title(t)!r}")

print("== 5. workflow recognizer ==")
from app.rag.query_understanding.workflow_query_recognizer import recognize_workflow_query

for q in tests:
    try:
        print(f"  {q[:55]!r} -> {recognize_workflow_query(q)}")
    except Exception as e:
        print(f"  ERR {type(e).__name__}: {str(e)[:200]}")
