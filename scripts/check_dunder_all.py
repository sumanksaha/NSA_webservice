"""Repo-wide check for ``__all__`` entries that are not string literals.

``__all__ = [SomeName, other_name]`` is syntactically valid but evaluates each
entry at import time, so a name defined *below* the ``__all__`` block raises
``NameError`` and the module cannot be imported at all. An over-eager
auto-quoting pass that strips quotes rather than adding them produces exactly
this.

Usage: ``python scripts/check_dunder_all.py``
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("app", "evaluation", "kg", "scripts")


def _iter_files():
    for pkg in PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        yield from sorted(root.rglob("*.py"))


def main() -> int:
    bad: list[tuple[str, int, list[str]]] = []
    checked = 0
    for f in _iter_files():
        try:
            tree = ast.parse(f.read_bytes().decode("utf-8"))
        except Exception:
            continue  # byte-compile pass reports unreadable files
        checked += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "__all__" not in targets:
                continue
            value = node.value
            if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                continue
            offenders = [
                f"{el.lineno}:{ast.unparse(el)}"
                for el in value.elts
                if not isinstance(el, ast.Constant) or not isinstance(el.value, str)
            ]
            if offenders:
                bad.append((str(f.relative_to(PROJECT_ROOT)), node.lineno, offenders))

    print(f"checked {checked} files")
    for rel, line, offenders in bad:
        print(f"  FAIL {rel}:{line}  non-string __all__ entries: {', '.join(offenders[:6])}")
    if not bad:
        print("  all clean")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
