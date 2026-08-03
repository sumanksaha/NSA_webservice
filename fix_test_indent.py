"""Fix indentation issues in test_search.py."""
import re

f = r"C:\github\NSA_webservice\tests\test_search.py"
with open(f, "r") as fh:
    lines = fh.readlines()

# Fix specific lines that got wrong indentation from editor edits
fixes = {
    297: "            results = search(\"Ghost\")\n",  # was 24 spaces
    448: "            assert len(results) == 0\n",     # was 24 spaces
}

for lineno, fix in fixes.items():
    if lineno <= len(lines):
        old = lines[lineno - 1].rstrip("\r\n")
        new = fix.rstrip("\n")
        if old.strip() == new.strip() and old != new:
            print(f"Line {lineno}: '{old[:40]}...' -> '{new[:40]}...'")
            lines[lineno - 1] = new + "\n"
        else:
            print(f"Line {lineno}: no change needed or mismatch")
            print(f"  old: '{old[:40]}'")
            print(f"  new: '{new[:40]}'")

with open(f, "w") as fh:
    fh.writelines(lines)
print("Done")
