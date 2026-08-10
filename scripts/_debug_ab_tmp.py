import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.metadata_extractor.regex_library as rx

title = "Food Safety and Standards (Alcoholic Beverages) Regulations, 2018\n"
reg_pat = rx.DOCUMENT_TYPE_PATTERNS[1][1]  # regulation
print("regulation matches:", bool(reg_pat.search(title)))

# Trace the title-case branch manually
import re

W = r"[ \t]+"
WC = r"[A-Za-z0-9'&,()\- \t]*?"
tc = re.compile(
    r"^\s*(?:(?-i:THE|The)" + W + r")?"
    r"(?:(?-i:[A-Z])" + WC + W + r"(?:" + WC + W + r")+)(?-i:[A-Z])(?-i:Regulations?)"
    + r"[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)
print("title-case branch matches:", bool(tc.search(title)))

# without the redundant (?-i:[A-Z]) before keyword
tc2 = re.compile(
    r"^\s*(?:(?-i:THE|The)" + W + r")?"
    r"(?:(?-i:[A-Z])" + WC + W + r"(?:" + WC + W + r")+)(?-i:Regulations?)"
    + r"[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)
print("title-case branch (no extra [A-Z]) matches:", bool(tc2.search(title)))
