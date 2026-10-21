"""WF-4 gate — the versioned multi-reference bundle (``evaluation/build_references_v4``).

The bundle is the single reference source for v4 components (design review F7).
These tests pin the freeze discipline: deterministic rebuilds, additive-only
migration, verified content checksum, complete qid coverage, and the
consumer-import ban (scoring consumers read the bundle, not the v1 shapes).

Per ``Ruler_Fix_Workflow.md`` WF-4 and ``evaluation/ruler`` R2/R3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.build_references_v4 import (
    BUNDLE_PATH,
    BUNDLE_VERSION,
    CHECKSUM_KEY,
    build_bundle,
    content_sha256,
    verify_bundle,
)


def _live_bundle() -> dict:
    assert BUNDLE_PATH.exists(), "run `python -m evaluation.build_references_v4` once before these tests"
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))


def test_rebuild_is_byte_deterministic(tmp_path: Path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    build_bundle(first)
    build_bundle(second)
    assert first.read_bytes() == second.read_bytes(), "a rebuild must not change the bundle (R2)"


def test_content_checksum_verified_and_tamper_detected(tmp_path: Path):
    bundle_path = tmp_path / "refs.json"
    result = build_bundle(bundle_path)
    assert result["ok"] and result["status"] == "frozen"

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle[CHECKSUM_KEY] == content_sha256(bundle)

    # tamper: edit one conclusion in place and re-verify
    qid = sorted(bundle["questions"])[0]
    bundle["questions"][qid]["conclusions"].append("tamper widening")
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    verdict = verify_bundle(bundle_path)
    assert not verdict["ok"] and verdict["status"] == "CORRUPT"


def test_coverage_and_shape_of_live_bundle():
    bundle = _live_bundle()
    from evaluation.benchmark import load_questions

    questions = {q.raw["question_id"]: q for q in load_questions()}
    assert bundle["questions"].keys() == questions.keys(), "one record per benchmark qid, none extra"
    assert bundle["meta"]["n_qids"] == len(questions)
    # the live bundle may legitimately move past the build-time constant (WF-5
    # applied versioned lexicon bans); require a valid, monotonic semver:
    version = str(bundle["meta"]["bundle_version"])
    parts = version.split(".")
    assert len(parts) == 3 and all(part.isdigit() for part in parts), f"bundle_version not semver: {version!r}"
    assert tuple(map(int, parts)) >= tuple(map(int, BUNDLE_VERSION.split("."))), (
        "bundle_version must never regress below the builder constant"
    )

    for qid, rec in bundle["questions"].items():
        assert rec["conclusions"], f"{qid} must carry at least the v1 conclusion"
        assert rec["expected_abstain"] == bool(questions[qid].insufficient_evidence)
        assert isinstance(rec["concepts"], list)
        # no duplicate conclusions (case/whitespace-normalized)
        normalized = [" ".join(c.lower().split()) for c in rec["conclusions"]]
        assert len(normalized) == len(set(normalized)), f"{qid} has duplicate conclusions"


def test_additive_migration_superset_and_marker_carryover():
    bundle = _live_bundle()
    overlay = json.loads(
        (Path(__file__).parent.parent / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8")
    )
    widened = overlay.get("widened_conclusions") or {}

    migrated = 0
    reported = {r["qid"]: r["reasons"] for r in bundle["meta"].get("migration_reported") or []}
    for qid, entry in widened.items():
        add = " ".join(str(entry.get("add") or "").split())
        if not add:
            continue
        rec = bundle["questions"][qid]
        if qid in reported:
            # mechanically rejected at migration (e.g. duplicates v1, "nothing
            # widened"): the bundle records the reasons instead of stamping it
            assert rec["provenance"]["v2_overlay"] is False
            continue
        assert add in rec["conclusions"], f"{qid} overlay widening must survive migration additively"
        assert rec["provenance"]["v2_overlay"] is True
        migrated += 1
    assert migrated == bundle["meta"]["n_widened_migrated"]

    # v1 conclusions never narrowed away
    from evaluation.benchmark import load_questions

    for q in load_questions():
        v1 = " ".join((q.acceptable_conclusion or "").split())
        if v1:
            assert bundle["questions"][q.raw["question_id"]]["conclusions"][0] == v1

    assert "banned_abstain_markers" in bundle, "C3 refinement (WF-5) edits this bundle section only"
    for qid in overlay["banned_abstain_markers"]["applies_to_qids"]:
        assert qid in bundle["banned_abstain_markers"]["applies_to_qids"]


def test_scoring_consumer_imports_bundle_only():
    """F7 gate: scoring consumers must not touch the v1 reference shapes directly."""
    banned_tokens = ("acceptable_conclusion", "gold_concepts")
    for consumer in ("evaluation/evaluator_v4.py", "evaluation/measure_component_rule_identity.py"):
        source = (Path(__file__).parent.parent / consumer).read_text(encoding="utf-8")
        hits = [token for token in banned_tokens if token in source]
        assert not hits, f"{consumer} must consume the bundle, not glean v1 shapes directly: {hits}"


def test_ruler_publishes_the_live_bundle_as_frozen():
    from evaluation.ruler import publication_header, reference_bundle_checksum

    assert reference_bundle_checksum()["status"] == "frozen"
    header = publication_header()
    assert header["reference_bundle"]["sha256"]
    assert header["reference_bundle"]["status"] == "frozen"
