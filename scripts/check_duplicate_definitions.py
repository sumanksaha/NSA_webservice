"""Find modules that define the same top-level name twice.

The defect
----------
Python resolves a name at *last* definition, so re-appending a class or
function to a module silently replaces the earlier one. Nothing warns, the
import succeeds, and the shadowed version keeps its own tests in the file --
which then fail for reasons that point nowhere near the real cause.

The instance this was built from
--------------------------------
``evaluation/evidence_set_selector.py`` had 260 lines appended, duplicating
``_build_hierarchy_map``, ``LegalStructureDiversitySelector``,
``HierarchyAwareSelector``, ``HybridEvidenceSetSelector``,
``candidates_to_arm_result``, ``_hhi`` and ``compute_redundancy``. The
appended copies dropped three pieces of logic:

* ``HierarchyAwareSelector.select`` lost ``hierarchy_level`` from its sort
  key, so depth ordering collapsed into score ordering;
* ``compute_redundancy`` lost the ``unique_keys == 1`` case, reporting
  ``0.75`` where the contract says ``1.0``;
* ``HybridEvidenceSetSelector.select`` lost its Phase-1 early exit, so Phase 1
  consumed every slot and KG complementarity never ran.

All three presented as "the new code has bugs". The new code was a
near-verbatim copy of working code with the substance removed.

Usage: ``python scripts/check_duplicate_definitions.py``
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("app", "evaluation", "kg", "scripts", "tests")


def main() -> int:
    bad: list[tuple[str, list[str]]] = []
    checked = 0
    for pkg in PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(f.read_bytes().decode("utf-8", errors="replace"))
            except Exception:
                continue
            checked += 1
            seen: dict[str, list[int]] = {}
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    seen.setdefault(node.name, []).append(node.lineno)
            dupes = {name: lines for name, lines in seen.items() if len(lines) > 1}
            if dupes:
                detail = "; ".join(f"{n} at {lines}" for n, lines in sorted(dupes.items()))
                bad.append((str(f.relative_to(PROJECT_ROOT)), detail))

    print(f"checked {checked} files")
    for rel, detail in bad:
        print(f"  FAIL {rel}\n        {detail}")
    if not bad:
        print("  all clean")
    else:
        print(f"\n  {len(bad)} module(s) shadow an earlier definition; the LAST one wins.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
