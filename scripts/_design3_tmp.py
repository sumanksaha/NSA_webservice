import re

W = r"[ \t]+"
WC = r"[A-Za-z0-9'&,()\- \t]*?"

# paren-tail branch: 1+ words ending with ')' then keyword, year at line end
# e.g. "Foods) Regulations, 2017.", "Food Additives) Regulations, 2011."
PAREN_TAIL = re.compile(
    r"^\s*(?:(?-i:THE|The)" + W + r")?"
    r"(?:[A-Za-z0-9'&,\-]+\))" + W + WC + r"?[ \t]*"
    r"(?-i:(?:Act|Regulations?|Rules?|Bill))"
    r"[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)

cases = [
    # True positives — must match
    ("Foods) Regulations, 2017.", True),
    ("Foods) Regulations, 2018.", True),
    ("Food Additives) Regulations, 2011.", True),
    ("Registration of Food Businesses) Regulations, 2011.", True),
    ("Food Business) Amendment Regulations, 2021.", True),
    # False positives — must NOT match
    ("Standards Act, 2006", False),          # 1-word plain lead-in, no paren
    ("Safety and Standards Act, 2006", False),
    ("Act, 2006", False),
    ("the Food Safety and Standards Act, 2006", False),  # lowercase lead
    ("In exercise of the powers conferred by section 92 of the Food", False),
    ("Subject: Food Safety and Standards (Advertising and Claims) Regulations, 2021", False),  # "Subject:" blocks
    ("G.S.R. 78(E).—The following draft of certain regulations", False),
    ("REGULATIONS 2011", False),            # all-caps bare — handled by other branch
    ("ACT, 2026", False),                   # all-caps bare — other branch
    ("First Amendment Regulations, 2016.", False),  # no paren
    # Edge: paren mid-line but not at line start
    ("the Food Safety and Standards (Advertising) Regulations, 2018.", False),
    ("(1) These regulations may be called the Food Safety and Standards (Organic", False),
]

ok = 0
for label, expected in cases:
    matched = PAREN_TAIL.search(label) is not None
    good = matched == expected
    ok += good
    print(f"{'OK ' if good else 'FAIL'} expected={expected!s:5s} got={matched!s:5s} | {label}")
print(f"\n{ok}/{len(cases)}")
