"""Repair scripts that rewrite source on import by adding ``__main__`` guards.

Why
---
``scripts/check_import_side_effects.py`` found 35 committed modules that called
``open(<some>.py, "w")`` in *module scope*. They sat inside importable packages
(``app/rag/evaluation/``, ``app/rag/feedback/``, ``tests/``, ``evaluation/``)
with no ``if __name__ == "__main__":`` guard, so any package walk - a linter, an
IDE indexer, a test collector, another agent - silently rewrote the target
file. This destroyed ``workflow_evaluator.py`` (9232 B -> 38 B) and stripped
the quotes off two ``__all__`` blocks, twice re-breaking files that had just
been repaired. The orphan fragment modules were deleted on 2026-10-20
(ADR-0015); this repair tool remains for any new import-time writer.

What it does
------------
For each offender, everything after the imports is wrapped in ``def _main()``
and called under a ``__main__`` guard. Running the file as a script does
exactly what it did before; *importing* it now does nothing.

The transform is line-based rather than an AST rewrite so that comments,
blank lines and the original formatting of the generated blocks survive
intact - these are scaffolding whose exact output matters.

Guard rails: every file is compiled after the transform, and the number of
module-scope writes must drop to zero. Any file that fails to compile is
restored from the pre-transform copy.

Usage: ``python scripts/guard_import_side_effects.py [--apply]``
       (dry-run without ``--apply``)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_import_side_effects import _scan_module_level_writes  # noqa: E402

HEADER = '"""One-shot codegen script.\n\nWrapped in a __main__ guard: importing this module used to rewrite\nits target .py file as a side effect.\n"""\n'


def _first_executable_line(tree: ast.Module) -> int:
    """1-based line where the real script body starts (after imports/docstring)."""
    body = tree.body
    idx = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        idx = 1  # module docstring
    while idx < len(body):
        node = body[idx]
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            idx += 1
            continue
        break
    # Skip a leading __future__ import that is not first (rare, but legal).
    while idx < len(body) and isinstance(body[idx], (ast.Import, ast.ImportFrom)):
        if isinstance(body[idx], ast.ImportFrom) and body[idx].module == "__future__":
            idx += 1
        else:
            break
    return body[idx].lineno if idx < len(body) else -1


def transform(src: str) -> str | None:
    """Return the guarded source, or None when the file needs no change."""
    tree = ast.parse(src)
    if not _scan_module_level_writes(tree):
        return None
    start = _first_executable_line(tree)
    if start == -1:
        return None

    lines = src.splitlines()
    if not lines:
        return None

    prefix = lines[: start - 1]
    body = lines[start - 1 :]

    # Indent the body by one level; blank lines stay blank so trailing
    # whitespace is not introduced.
    indented = [("    " + ln) if ln.strip() else "" for ln in body]

    out: list[str] = []
    out.extend(prefix)
    if out and out[-1].strip():
        out.append("")
    out.append("")
    out.append("def _main() -> None:")
    out.extend(indented)
    out.append("")
    out.append("")
    out.append('if __name__ == "__main__":')
    out.append("    _main()")
    result = "\n".join(out).rstrip("\n") + "\n"

    # Must still parse, and must no longer write at import time.
    new_tree = ast.parse(result)
    if _scan_module_level_writes(new_tree):
        return None
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()

    from scripts.check_import_side_effects import PACKAGES

    offenders: list[Path] = []
    for pkg in PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(f.read_bytes().decode("utf-8", errors="replace"))
            except Exception:
                continue
            if _scan_module_level_writes(tree):
                offenders.append(f)

    changed, skipped = [], []
    for f in offenders:
        src = f.read_bytes().decode("utf-8", errors="replace")
        try:
            new = transform(src)
        except Exception as exc:
            skipped.append((f, f"transform error: {exc}"))
            continue
        if new is None:
            skipped.append((f, "no safe transform"))
            continue
        try:
            compile(new, str(f), "exec", dont_inherit=True)
        except SyntaxError as exc:
            skipped.append((f, f"would not compile: {exc}"))
            continue
        changed.append((f, src, new))

    rel = lambda p: str(p.relative_to(PROJECT_ROOT))  # noqa: E731
    print(f"offenders: {len(offenders)}   transformable: {len(changed)}   skipped: {len(skipped)}")
    for f, why in skipped:
        print(f"  SKIP {rel(f)}: {why}")

    if not args.apply:
        print("\ndry run - re-run with --apply to write")
        return 0

    for f, src, new in changed:
        f.write_text(new, encoding="utf-8", newline="")
    print(f"\nwrote {len(changed)} guarded files")

    # Verify: no module-scope writes remain anywhere.
    remaining = 0
    for pkg in PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(f.read_bytes().decode("utf-8", errors="replace"))
            except Exception:
                continue
            if _scan_module_level_writes(tree):
                print(f"  STILL UNSAFE {rel(f)}")
                remaining += 1
    print(f"remaining unsafe modules: {remaining}")
    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
