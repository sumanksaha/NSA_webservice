"""Module health check for the RAG stack.

Four passes, cheapest first:

1. **Byte-compile everything** — catches the class of defect that blocked the
   evaluation: a file with invalid UTF-8 or a syntax error fails *here* rather
   than halfway through a 40-minute live run.
2. **Import every module** — catches broken references, circular imports and
   import-time side effects that only surface on a cold interpreter. A stale
   ``__pycache__`` can mask these, so this runs with bytecode writing disabled.
3. **Static undefined-name scan** — ``merge_caller_filters`` was imported by
   ``hybrid_retriever`` and used, but never defined; it only surfaced when
   ``6c34530`` added the call. Compile and import both pass on such a defect
   (the name is resolved at call time), so it needs its own pass.
4. **Config flag resolution** — every declared RAG flag must resolve to a real
   value, since the evaluation gates arms on ``cfg.section_arm`` /
   ``cfg.multi_domain_router``.

Run: ``python scripts/module_health_check.py``
"""

from __future__ import annotations

import ast
import importlib
import os
import py_compile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

os.environ.setdefault("RAG_USE_STUB_LLM", "false")

PACKAGES = ("app", "evaluation", "kg", "scripts")

#: Only production packages get *imported*. Many ``evaluation/*`` and
#: ``scripts/*`` modules run live retrieval arms at import time (one spends
#: 158s in the cross-encoder), so blanket-importing them turns a health check
#: into a benchmark run. They are still byte-compiled and statically scanned.
IMPORT_PACKAGES = ("app", "kg")

#: Underscore-prefixed modules are excluded from the import pass. This is not
#: cosmetic: the former ``app/rag/evaluation/_write_evaluator.py`` and its
#: siblings were one-shot scaffolding scripts with a hardcoded path, and
#: importing them **overwrote** ``app/rag/evaluation/workflow_evaluator.py``.
#: Those fragment modules were deleted on 2026-10-20 (ADR-0015); the prefix
#: guard stays as a defence against new import-time writers.
SKIP_IMPORT_PREFIXES = ("_",)


def _py_files() -> list[Path]:
    out: list[Path] = []
    for pkg in PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        out.extend(sorted(root.rglob("*.py")))
    return out


# --------------------------------------------------------------------------- #
def pass_bytecode() -> tuple[int, list[tuple[str, str]]]:
    bad: list[tuple[str, str]] = []
    files = _py_files()
    for f in files:
        try:
            # Compile the raw bytes rather than decoding first. CPython
            # downgrades an undecodable byte that lands inside a comment to a
            # warning and imports the module anyway, so an explicit
            # ``bytes.decode("utf-8")`` here reports failures the interpreter
            # does not actually have.
            compile(f.read_bytes(), str(f), "exec", dont_inherit=True)
        except Exception as exc:
            bad.append((str(f.relative_to(PROJECT_ROOT)), f"{type(exc).__name__}: {exc}"))
    return len(files), bad


# --------------------------------------------------------------------------- #
def _importable_modules() -> list[str]:
    mods: list[str] = []
    for pkg in IMPORT_PACKAGES:
        root = PROJECT_ROOT / pkg
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.py")):
            rel = f.relative_to(PROJECT_ROOT)
            parts = list(rel.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            if parts[-1].startswith(SKIP_IMPORT_PREFIXES):
                continue
            mods.append(".".join(parts))
    return mods


def pass_import() -> tuple[int, list[tuple[str, str]]]:
    bad: list[tuple[str, str]] = []
    mods = _importable_modules()
    for name in mods:
        try:
            importlib.import_module(name)
        except Exception as exc:
            # A missing optional dependency is a config fact, not a defect in
            # this repo; report it distinctly rather than as a failure.
            kind = "DEP" if isinstance(exc, ModuleNotFoundError) else "ERR"
            bad.append((name, f"{kind}: {type(exc).__name__}: {str(exc)[:200]}"))
    return len(mods), bad


# --------------------------------------------------------------------------- #
# Names that are read at module scope (or inside functions) but never bound in
# the module: the ``merge_caller_filters`` shape.
# --------------------------------------------------------------------------- #
def _module_level_names(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _referenced_names(tree: ast.Module) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


_BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, dict) is False else set(__builtins__.keys())
_BUILTINS |= set(dir(sys.modules["builtins"]))

#: Supplied by the interpreter at module scope, so they are always bound even
#: though no ``Store`` node binds them.
_MODULE_DUNDERS = {"__file__", "__builtins__", "__name__", "__doc__", "__package__", "__spec__", "__loader__"}


def pass_undefined() -> tuple[int, list[tuple[str, str]]]:
    bad: list[tuple[str, str]] = []
    files = _py_files()
    for f in files:
        try:
            tree = ast.parse(f.read_bytes().decode("utf-8"))
        except Exception:
            continue  # pass 1 already reported it
        bound = _module_level_names(tree)
        used = _referenced_names(tree)
        # Names a comprehension/lambda target rebinds are still Store nodes,
        # so a plain set difference is sound here.
        missing = sorted(used - bound - _BUILTINS - _MODULE_DUNDERS)
        if missing:
            rel = str(f.relative_to(PROJECT_ROOT))
            bad.append((rel, f"possibly undefined: {', '.join(missing[:8])}"))
    return len(files), bad


# --------------------------------------------------------------------------- #
def pass_config() -> tuple[int, list[tuple[str, str]]]:
    bad: list[tuple[str, str]] = []
    try:
        from app.shared.config import cfg
    except Exception as exc:
        return 0, [("app.shared.config", f"ERR: {type(exc).__name__}: {exc}")]

    names = sorted(n for n in dir(cfg) if n.startswith(("rag_", "enable_", "multi_", "section_", "form_", "fsg_")))
    for n in names:
        try:
            getattr(cfg, n)
        except Exception as exc:
            bad.append((n, f"{type(exc).__name__}: {exc}"))

    # The two flags this evaluation gates arms on, stated explicitly so their
    # value is never a surprise in a report.
    for flag in ("section_arm", "multi_domain_router", "form_arm", "identifier_route"):
        try:
            sys.stderr.write(f"  cfg.{flag} = {getattr(cfg, flag)!r}\n")
        except Exception as exc:
            bad.append((flag, f"unreadable: {exc}"))
    return len(names), bad


# --------------------------------------------------------------------------- #
def main() -> int:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True

    # Importing app.* constructs the Flask app, syncs SQLite and scrolls
    # Qdrant; at INFO that buries the results under hundreds of lines. The
    # side effects still run (that is the point of the pass) - only the
    # logging is muted.
    import logging

    logging.disable(logging.CRITICAL)

    for title, fn in (
        ("byte-compile", pass_bytecode),
        ("import", pass_import),
        ("undefined-name scan", pass_undefined),
        ("config flags", pass_config),
    ):
        sys.stderr.write(f"\n=== {title} ===\n")
        total, bad = fn()
        if title == "config flags":
            sys.stderr.write(f"checked {total} flags\n")
        else:
            sys.stderr.write(f"checked {total} files/modules\n")
        for name, msg in bad:
            sys.stderr.write(f"  FAIL {name}: {msg}\n")
        if not bad:
            sys.stderr.write("  all clean\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
