"""Find importable modules that WRITE source files at import time.

The defect
----------
A ``.py`` file that calls ``open(path, "w")`` / ``"a"`` in **module scope** --
not inside a function, not inside a class, not inside an
``if __name__ == "__main__":`` guard -- executes on *import*. Inside an
importable package that turns any package walk into a code generator: a
linter, an IDE indexer, a test collector, or another agent silently rewrites
your source.

The incident this was built for
------------------------------
``app/rag/evaluation/_write_evaluator.py`` was four lines::

    path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"
    with open(path, "w", encoding="utf-8") as f:
        f.write("Touched - will write full content next")

No guard. No ``__main__``. A hardcoded absolute path. Importing it replaced a
9 KB working module with 38 bytes. Its siblings (``_part1..8.py``,
``_insert_*.py``, ``_fix_order.py``) rebuilt the file in stages; the last
stage to run won, so the package's contents depended on import order. That
corrupted ``workflow_evaluator.py`` repeatedly during a live evaluation, and
twice re-broke a file that had already been repaired.

Those one-shot fragment modules were deleted on 2026-10-20 (ADR-0015). This
scanner remains the guard against reintroducing an import-time source writer.

Two aggravating factors worth keeping in mind when reading the report:

* **Hardcoded absolute paths** make the damage machine-specific -- it never
  reproduces in CI or on another checkout, so a green pipeline proves nothing.
* **Underscore prefixes hide the blast radius** from tooling that only looks
  at ``import``-able or ``test_``-able names, while ``python -c "import ..."``
  still fires them.

Usage: ``python scripts/check_import_side_effects.py``
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("app", "kg", "evaluation", "scripts", "tests")

WRITE_MODES = ("w", "a", "r+", "w+", "a+")


def _string_args(call: ast.Call, consts: dict[str, str]) -> list[str]:
    """String arguments of ``open(...)``, resolving single-assignment names.

    The write target is almost always a variable (``path = r"C:/...py"``), so
    matching literals alone reports "all clean" on exactly the files that are
    most dangerous.
    """
    out: list[str] = []
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            out.append(a.value)
        elif isinstance(a, ast.Name) and a.id in consts:
            out.append(consts[a.id])
    for kw in call.keywords:
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            out.append(kw.value.value)
    return out


def _module_level_string_consts(tree: ast.Module) -> dict[str, str]:
    """``name -> string`` for module-scope assignments of a plain literal."""
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            consts[node.target.id] = node.value.value
    return consts


def _is_write_mode(call: ast.Call) -> bool:
    """True when ``open(...)`` opens for writing or appending.

    ``mode`` arrives positionally (``open(p, "w")``) or by keyword
    (``open(file=p, mode="w")``) and defaults to ``"r"``. It is seeded before
    either branch so a keyword-only call cannot leave it unbound.
    """
    mode = "r"
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            mode = kw.value.value
    if mode == "r" and len(call.args) >= 2:
        second = call.args[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            mode = second.value
    return any(m in mode for m in WRITE_MODES)


def _writes_source(target: str) -> bool:
    return target.lower().endswith(".py")


def _scan_module_level_writes(tree: ast.Module) -> list[int]:
    """Line numbers of module-scope ``open(.py, write)`` calls.

    Descends through ``if``/``try``/``with`` (which may still be module scope)
    but stops at function and class bodies, and stops entirely inside a
    ``__main__`` guard.
    """
    consts = _module_level_string_consts(tree)
    hits: list[int] = []

    def is_main_guard(node: ast.If) -> bool:
        """``if __name__ == "__main__":`` — judged on this node's own test.

        A subtree search cannot be used here: at the top-level call the node
        is the whole Module, so a guard *anywhere* in the file would mark
        every statement guarded. Guard state is carried down the walk instead.
        """
        test = node.test
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and any(isinstance(op, ast.Eq) for op in test.ops)
        )

    def record(call: ast.Call) -> None:
        if getattr(call.func, "id", None) != "open" or not _is_write_mode(call):
            return
        if any(_writes_source(s) for s in _string_args(call, consts)):
            hits.append(call.lineno)

    def walk(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            # Function and class bodies do not run at import time.
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            child_guarded = guarded or (isinstance(child, ast.If) and is_main_guard(child))
            if not child_guarded:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                    record(child.value)
                if isinstance(child, (ast.With, ast.AsyncWith)):
                    for item in child.items:
                        if isinstance(item.context_expr, ast.Call):
                            record(item.context_expr)
            walk(child, child_guarded)

    walk(tree, False)
    return hits


def main() -> int:
    offenders: list[tuple[str, list[int]]] = []
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
            hits = _scan_module_level_writes(tree)
            if hits:
                offenders.append((str(f.relative_to(PROJECT_ROOT)), sorted(set(hits))))

    print(f"checked {checked} files")
    hardcoded = 0
    for rel, lines in offenders:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        # An absolute Windows/POSIX drive-letter path baked into the module.
        is_hardcoded = any(tok in text for tok in ("C:/", "C:\\", "D:/", "/home/", "/Users/"))
        hardcoded += 1 if is_hardcoded else 0
        flag = "  HARD-CODED ABS PATH" if is_hardcoded else ""
        print(f"  FAIL {rel}:{lines[0]}  module-scope write to .py on import (x{len(lines)}){flag}")
    if not offenders:
        print("  all clean")
    else:
        print(f"\n  {len(offenders)} module(s) rewrite source on import; {hardcoded} with a hardcoded absolute path")
        print("  Any tool that imports this package will rewrite these files.")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
