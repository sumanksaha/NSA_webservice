import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pdfplumber


def safe(s):
    return s.encode("ascii", "backslashreplace").decode("ascii")


for f in [
    "FSSAI_rules documents/Nutraceuticals_Regulations.pdf",
    "FSSAI_rules documents/Draft Notification-FSS_FPSFA_Amendment Regulations_2026_Minor seed oils_Edible seeds_Appendix A.pdf",
]:
    with pdfplumber.open(f) as pdf:
        print("=" * 20, f.split("/")[-1], f"pages={len(pdf.pages)}")
        for pi, page in enumerate(pdf.pages[:12]):
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                if re.search(r"REGULATIONS?\b", line, re.IGNORECASE):
                    print(f"  p{pi}: {safe(line[:95])}")
        print()
