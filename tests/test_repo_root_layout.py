"""Keep one-off scripts and loose documents out of the repo root.

The root used to accumulate 58 scratch scripts (``check_*.py``, ``_*.py``,
``test_*.py``) and 30 ad-hoc reports. Every root-level script permanently bought
itself a `per-file-ignores` lint exemption in `pyproject.toml`, and every loose
report sat outside `docs/`, invisible to `docs/INDEX.md` (the navigation layer
that `AGENTS.md` points every agent at).

Both crowds were moved out:

* one-off scripts -> ``scripts/oneoff/`` (shared ``scripts/**/*`` lint rules)
* reports, plans, audits, experiment designs -> ``docs/<theme>/`` (indexed)

These tests freeze what is left. Deleting a file is fine (the assertions are
subset checks); *adding* a root-level ``*.py`` or ``*.md`` now requires
consciously extending an allowlist below, which makes the cost visible in
review.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Real application entry points — these belong at the root by design.
ENTRYPOINTS = frozenset({"app.py", "asgi.py"})

#: Frozen inventory of pre-existing root-level scripts.  Additions need review;
#: removals are automatically fine (the assertion is a subset check).
#: Empty since the ``scripts/oneoff/`` move — keep it that way.
ROOT_SCRIPT_ALLOWLIST: frozenset[str] = frozenset()

#: Root-level documents that must stay put, each for a specific reason.
ROOT_DOC_ALLOWLIST = frozenset({
    # Front page / community health files that convention places at the root.
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "README.md",
    "SECURITY.md",
    # Always-loaded steering docs: agents read these by their root path.
    "AGENTS.md",
    "CLAUDE.md",
    "CODING_STANDARDS.md",
    "CONTEXT.md",
    # Canonical phase plan: cited by module docstrings and migrations as
    # "plan.md Phase N" / "task.md Phase N"; moving them would orphan ~30
    # source comments for no navigational gain.
    "plan.md",
    "task.md",
    # Runtime data, not documentation: loaded from the workspace root at
    # import time by app/utils/fso_data.py, app/utils/sections_data.py,
    # app/rag/retrieval/form_references.py and the scripts that feed them.
    "FBO_actionmode.md",
    "fso_list.md",
    "fss_sections.md",
    "sample_workflow.md",
})

ALLOWED_SCRIPTS = ROOT_SCRIPT_ALLOWLIST | ENTRYPOINTS


def test_no_new_root_level_scripts() -> None:
    present = {p.name for p in ROOT.glob("*.py")}
    unexpected = sorted(present - ALLOWED_SCRIPTS)
    assert not unexpected, (
        "new root-level scripts found: "
        + ", ".join(unexpected)
        + "\nOne-off scripts belong in scripts/oneoff/ (shared lint rules) — "
        "extend this allowlist only for a genuine root entry point."
    )


def test_no_new_root_level_docs() -> None:
    present = {p.name for p in ROOT.glob("*.md")}
    unexpected = sorted(present - ROOT_DOC_ALLOWLIST)
    assert not unexpected, (
        "new root-level documents found: "
        + ", ".join(unexpected)
        + "\nReports, plans and audits belong in docs/<theme>/ and must be "
        "linked from docs/INDEX.md — extend this allowlist only for a file "
        "that genuinely has to live at the root (runtime data, steering doc, "
        "or a convention-mandated community health file)."
    )
