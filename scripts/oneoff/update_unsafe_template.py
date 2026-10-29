"""Rewrite Unsafe_file.adoc (committed 206-line base):
- header comment now describes the Prayer document (prohibition order moved to Prohibition_order.adoc)
- table/body comments refreshed
- fix the per-packet sample equation on the Whereas line
- remove the prohibition-order section and add a signature + date block to the prayer
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

path = "app/case_file_generator/templates/case_file_generator/Unsafe_file.adoc"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

print("total lines before:", len(lines))

def find_after(index, predicate):
    for i in range(index, len(lines)):
        if predicate(lines[i]):
            return i
    raise ValueError("anchor not found")

# ---------------- Find all anchors in the ORIGINAL list ----------------
first_dash = find_after(83, lambda l: l.startswith("// ─"))
second_dash = find_after(first_dash + 1, lambda l: l.startswith("// ─"))
table_comment_idx = find_after(second_dash, lambda l: "14-column table on page 1" in l)

whereas_idx = None
for i, l in enumerate(lines):
    if "Whereas, a sample namely" in l:
        whereas_idx = i
        break
assert whereas_idx is not None, "Whereas line anchor not found"

heading = "[.bold]#PROHIBITION ORDER — DIRECTED TO THE FOOD BUSINESS OPERATOR#"
heading_idx = find_after(second_dash, lambda l: heading in l)
# search backward for the preceding <<<
prohibition_break_idx = None
for i in range(heading_idx - 1, second_dash, -1):
    if lines[i].strip() == "<<<":
        prohibition_break_idx = i
        break
assert prohibition_break_idx is not None, "page-break before prohibition-order not found"

print(f"anchors: header={first_dash}-{second_dash}, table_comment={table_comment_idx}, "
      f"whereas={whereas_idx}, heading={heading_idx}, prohibition_break={prohibition_break_idx}")

# New header comment text (used in replacement 4 below).
new_header = [
    "// ─────────────────────────────────────────────────────────────────────────\n",
    "// Unsafe File — Prayer to the Designated Officer (ADR-001 .adoc template).\n",
    "//\n",
    "// This is the first of two documents for an UNSAFE sample. Page 1 holds ONLY\n",
    "// the 14-column offender/sample table (no header/letterhead); the renderer\n",
    "// (case_file_generator/adoc_renderer.py) post-processes the DOCX so page 1 is\n",
    "// a landscape section and every following page is portrait. The letter body\n",
    "// starts on page 2.\n",
    "//\n",
    "// The second document is the prohibition order (Prohibition_order.adoc),\n",
    "// rendered by its own endpoint (download_prohibition_order_docx) and issued\n",
    "// only to the FBO manufacturer for non-RCM unsafe cases. RCM (prepared/loose\n",
    "// food) cases cannot have a prohibition order — see routes.py.\n",
    "//\n",
    '// Design note ("barring logo and text of letter in the first page"):\n',
    "// This .adoc generates the Prayer to the Designated Officer for a sample\n",
    "// found UNSAFE. It does NOT embed the page-1 logo image (the letterhead\n",
    "// logo is supplied by stationery) and it does NOT reproduce the source cover\n",
    '// memo letter ("To / Letter No H/FCI31/2025-26 / Purnima Jaiswal /\n',
    '// PROHIBITION ORDER UNDER SEC 36(3)(B) … signature DESIGNATED FOOD CELL")\n',
    "// that precedes the prohibition order. The document starts at the case\n",
    '// number and the addressee ("To, The Designated Officer"); the court header\n',
    '// "Before the Ld. Adjudicating Officer, KMC" and cover-memo text have been\n',
    "// removed per the revised design.\n",
]

# ---------------- 5) Apply all replacements using ORIGINAL indices ----------------
# The replacements do not overlap. We apply the longest-range removal first
# (highest index) so that the remaining anchor positions stay valid.
#
#   replacement order:
#     1) prohibition removal  -> lines[prohibition_break_idx:]            (idx 159)
#     2) whereas equation     -> within single line (no length change)     (idx 146)
#     3) table comment        -> lines[table_comment_idx:table_comment_idx+4] (idx 121)
#     4) header comment       -> lines[first_dash:second_dash + 1]         (idx 83)

# 1) Remove the prohibition-order section (break -> EOF) and sign the prayer.
signature_block = [
    "\n",
    "[.signature]\n",
    "{{ food_safety_officer_name }}\n",
    "Food Safety Officer\n",
    "Kolkata Municipal Corporation\n",
    "\n",
    "[.header]\n",
    "Date - {{ authorization_date }}\n",
]
lines[prohibition_break_idx:] = signature_block

# 2) Fix the per-packet sample equation.
old_eq = "(X {{ packet_count }} ="
new_eq = "({{ per_packet_sample_quantity }} X {{ packet_count }} ="
assert old_eq in lines[whereas_idx], f"broken equation not found at idx {whereas_idx}"
lines[whereas_idx] = lines[whereas_idx].replace(old_eq, new_eq, 1)
print("fixed equation at line", whereas_idx + 1)

# 3) Update the page-1 table comment.
lines[table_comment_idx:table_comment_idx + 4] = [
    "// 14-column table on page 1: offender/sample details (table only; no letterhead)\n",
    "// No header/letterhead on page 1 per requirements — table only.\n",
    "// Rows are built in routes.py (_unsafe_offender_table_rows): one row for the\n",
    "// retailer, plus one for the manufacturer when they are separate entities.\n",
]

# 4) New header comment.
lines[first_dash:second_dash + 1] = new_header

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("total lines after:", len(lines))
print("LAST 12 LINES:")
for l in lines[-12:]:
    print(repr(l))
