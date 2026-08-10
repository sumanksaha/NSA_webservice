import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber

# Paren-tail candidate: line starts with word(s) ending in ')' then keyword+year at line end
PAREN_TAIL = re.compile(
    r"^\s*(?:[A-Za-z0-9'&,()\-]+\))[ \t]+(?:[A-Za-z0-9'&,()\-]*?[ \t]+)*(?-i:(?:Act|Regulations?|Rules?|Bill))[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)


def safe(s):
    return s.encode("ascii", "backslashreplace").decode("ascii")


must_stay_notification = [
    "273797-1.pdf",
    "6a1fd30fa8c01prohibition_final.pdf",
    "6a3c0a8fbaf61273797.pdf",
    "LicReg.pdf",
    "Gazette_Notification_Quality_Vegetable_Oil_03_11_2017-1.pdf",
]
print("=== risk check: paren-tail lines in must-stay-Notification docs ===")
for name in must_stay_notification:
    f = Path("FSSAI_rules documents") / name
    with pdfplumber.open(f) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:6])
    hits = [(m.start(), text[m.start():].splitlines()[0][:70]) for m in PAREN_TAIL.finditer(text)]
    print(f"  {name[:50]}: {len(hits)} paren-tail hits")
    for pos, line in hits[:4]:
        print(f"      pos={pos} | {safe(line)}")

print()
print("=== Nutraceuticals: where does the title live? ===")
f = Path("FSSAI_rules documents/Nutraceuticals_Regulations.pdf")
with pdfplumber.open(f) as pdf:
    for pi, page in enumerate(pdf.pages[:8]):
        txt = page.extract_text() or ""
        for line in txt.splitlines():
            if re.search(r"NUTRACEUTICAL", line, re.IGNORECASE):
                print(f"  p{pi}: {safe(line[:95])}")
print()

print("=== Organic: all title-bearing lines p0-3 ===")
f = Path("FSSAI_rules documents/Organic_Food_Regulations.pdf")
with pdfplumber.open(f) as pdf:
    for pi, page in enumerate(pdf.pages[:4]):
        txt = page.extract_text() or ""
        for line in txt.splitlines():
            if "Organic" in line:
                print(f"  p{pi}: {safe(line[:95])}")
