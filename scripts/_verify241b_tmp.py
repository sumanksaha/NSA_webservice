import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber

from app.metadata_extractor.extractors.base import DocumentTypeExtractor

ex = DocumentTypeExtractor()

ACT_SAMPLE = """
THE FOOD SAFETY AND STANDARDS ACT, 2006
ACT NO. 34 OF 2006
"""
NOTIF_SAMPLE = """
F. No. 1-4/Standards/SP(FSSAI)/2019
Food Safety and Standards Authority of India
(Advertising Section)
Notification
New Delhi, the 5th March, 2021

Subject: Food Safety and Standards (Advertising and Claims) Regulations, 2021

S.O. 1234(E).-In exercise of the powers conferred by section 92 of the Food
Safety and Standards Act, 2006, the Food Safety and Standards Authority of
India hereby makes the following regulations.
"""
checks = [
    ("ACT sample -> Act", ACT_SAMPLE, "Act"),
    ("Notification sample -> Notification", NOTIF_SAMPLE, "Notification"),
    # New: paren-tail wrapped-title fragments
    ("Foods) Regulations, 2017.", "Foods) Regulations, 2017.\n", "Regulation"),
    ("Food Additives) Regulations, 2011.", "Food Additives) Regulations, 2011.\n", "Regulation"),
    ("Food Business) Amendment Regulations, 2021.", "Food Business) Amendment Regulations, 2021.\n", "Regulation"),
    # Must NOT false-positive
    ("Standards Act, 2006 (1-word, no paren)", "Standards Act, 2006\n", None),
    ("Safety and Standards Act, 2006 (2-word, no paren)", "Safety and Standards Act, 2006\n", None),
    ("Act, 2006 (bare title-case)", "Act, 2006\n", None),
    ("Subject line with parens", "Subject: Food Safety and Standards (Advertising) Regulations, 2021\n", None),
    ("1. Short title... (numbered clause)", "1. Short title and commencement. (1) These regulations may be called the Food Safety and Stand\n", None),
]
for label, text, expected in checks:
    results = ex.extract(text)
    top = results[0][0]
    if expected is None:
        ok = top not in ("Act", "Regulation", "Rule", "Bill")
        exp = "not-instrument"
    else:
        ok = top == expected
        exp = expected
    print(f"{'OK ' if ok else 'FAIL'} {label:52s} -> {top:12s} (expected {exp}) | all={[r[0] for r in results[:3]]}")

print()
print("=== Real corpus (first 5 pages) ===")
from collections import Counter

counts = Counter()
for f in sorted(Path("FSSAI_rules documents").glob("*.pdf")):
    try:
        with pdfplumber.open(f) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:5])
    except Exception as exc:  # noqa: BLE001
        print("ERROR", f.name, exc)
        continue
    results = ex.extract(text)
    top = results[0][0]
    counts[top] += 1
    print(f"  {top:22s} {f.name[:58]}")
print()
print("DISTRIBUTION:", dict(counts))
