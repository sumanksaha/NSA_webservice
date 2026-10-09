"""Keep one-off scripts out of the repo root.

54 scratch scripts already sit at the root (``check_*.py``, ``_*.py``,
``test_*.py``), and `pyproject.toml` carries a matching `per-file-ignores`
entry for each — every root-level script permanently buys a lint exemption.

This test freezes that inventory: deleting a file is fine (a subset check), but
adding a new root-level ``*.py`` requires consciously extending the allowlist
below, which makes the cost visible in review. New one-offs belong in
``scripts/``, which already shares a single ignore rule.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Real application entry points — these belong at the root by design.
ENTRYPOINTS = frozenset({"app.py", "asgi.py"})

#: Frozen inventory of pre-existing root-level scripts.  Additions need review;
#: removals are automatically fine (the assertion is a subset check).
ROOT_SCRIPT_ALLOWLIST = frozenset({
    "_analyze_provisions.py",
    "_check_compile.py",
    "_eval_stats.py",
    "_extract_rejected.py",
    "_find_provisions.py",
    "_gen_stats.py",
    "_generate_templates.py",
    "_inspect_variants.py",
    "_prof_coverage.py",
    "_read_doc.py",
    "_review_sample.py",
    "_verify_coverage.py",
    "analyze_failures.py",
    "append_proh_enclosures.py",
    "check_config_access.py",
    "check_gold.py",
    "check_gold_status.py",
    "check_llm.py",
    "check_manual_answers.py",
    "check_pairwise.py",
    "check_stats.py",
    "count_csv.py",
    "create_gold_answers_v3.py",
    "create_prohibition_template.py",
    "deep_dive.py",
    "detailed_analyze.py",
    "dirty-index.py",
    "enumerate-dir-as-file.py",
    "extract_phase2.py",
    "files-to-paths.py",
    "final_check.py",
    "final_verification.py",
    "fix_route.py",
    "fixed-write_full_output.py",
    "gold_answer_data_part1.py",
    "gold_answer_data_part2.py",
    "improve_rejected.py",
    "improve_rejected_v2.py",
    "improve_rejected_v3.py",
    "insert_route.py",
    "inspect_adoc.py",
    "inspect_csv.py",
    "inspect_proh.py",
    "repair-dir-as-file.py",
    "temp.py",
    "test_e2e.py",
    "test_raw_underscore.py",
    "test_route_nesting.py",
    "test_underscore.py",
    "update_unsafe_template.py",
    "upsert_reg.py",
    "verify_gold_enriched.py",
})

ALLOWED = ROOT_SCRIPT_ALLOWLIST | ENTRYPOINTS


def test_no_new_root_level_scripts() -> None:
    present = {p.name for p in ROOT.glob("*.py")}
    unexpected = sorted(present - ALLOWED)
    assert not unexpected, (
        "new root-level scripts found: "
        + ", ".join(unexpected)
        + "\nOne-off scripts belong in scripts/ (shared lint rules) — extend this "
        "allowlist only for a genuine root entry point."
    )
