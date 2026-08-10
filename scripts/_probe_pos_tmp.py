import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber

# Probe: for each problem doc, find line-anchored instrument candidates
# (including 1-word lead-ins) and their char positions/fractions.

RELAXED = re.compile(
    r"^\s*(?:(?-i:THE|The)[ \t]+)?"
    r"(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t]+(?:[A-Za-z0-9'&,()\- \t]*?[ \t]+)*"
    r"(?-i:(?:Act|Regulations?|Rules?|Bill))"
    r"|[ \t]*[ \t]+(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?(?-i:(?:ACT|REGULATIONS?|RULES?|BILL)))"
    r"[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)

files = [
    "FSSAI_rules documents/Organic_Food_Regulations.pdf",
    "FSSAI_rules documents/Food_Fortification_Regulations.pdf",
    "FSSAI_rules documents/Nutraceuticals_Regulations.pdf",
    "FSSAI_rules documents/Compendium_Licensing_Regulations_04_08_2021.pdf",
    "FSSAI_rules documents/Licensing_Regulations-2.pdf",
]

for f in files:
    with pdfplumber.open(f) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:10])
    total = len(text)
    print("=" * 20, f.split("/")[-1], f"len={total}")
    for m in RELAXED.finditer(text):
        frac = m.start() / total
        line = text[m.start():].splitlines()[0][:70]
        print(f"  pos={m.start():7d} frac={frac:.2f} | {line}")
    print()
