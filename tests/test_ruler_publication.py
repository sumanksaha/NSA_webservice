"""WF-0 gate — the ruler publication rail (``evaluation/ruler``).

A number without its measurement context cannot circulate. These tests pin
the publication header contract: version present, reference-bundle checksum
explicit (frozen or ABSENT), boot rules carried verbatim, and validation
backed by raising — so a report claiming a ruler lineage but omitting the
context cannot ship.

Per ``Ruler_Fix_Workflow.md`` WF-0 and ``Evaluator_V4_Binary_Ruler_Design_Review.md``
§5.1 rules R2/R4.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.ruler import (
    BOOT_RULES,
    REFERENCE_BUNDLE_PATH,
    RULER_ID,
    RULER_VERSION,
    REQUIRED_BINARY_KEYS,
    RulerPublicationError,
    format_publication_header,
    publication_header,
    reference_bundle_checksum,
    validate_header,
    write_json_with_header,
)


def test_ruler_version_is_declared_and_prerelease():
    assert RULER_VERSION.startswith("v")
    # WF-8/WF-9 acceptance (determinism byte-identical + baseline frozen) has
    # landed: the -dev suffix is retired. A regression BACK to -dev would
    # silently undo the promotion — fail loudly instead.
    assert not RULER_VERSION.endswith("-dev"), (
        "the ruler was already promoted to v4.2 (WF-8/WF-9 acceptance); "
        "a new -dev state must come with its own recorded justification"
    )
    assert RULER_ID == "evaluator-v4"


def test_header_carries_version_reference_rules_and_thresholds():
    header = publication_header()
    assert header["ruler_version"] == RULER_VERSION
    assert list(header["reference_bundle"]) == ["path", "sha256", "status"]
    assert header["boot_rules"] == list(BOOT_RULES)
    # No inherited 0.5 (F4): calibration is pending, not defaulted.
    assert "0.5" not in json.dumps(header["thresholds"])
    assert header["required_binary_keys"] == list(REQUIRED_BINARY_KEYS)


def test_reference_bundle_is_frozen_and_ships_its_checksum():
    """WF-4 landed the bundle; ABSENT is no longer the live state (was the pre-WF-4 state)."""
    assert REFERENCE_BUNDLE_PATH.exists()
    bundle = reference_bundle_checksum()
    assert bundle["status"] == "frozen" and bundle["sha256"]
    header = publication_header()
    assert validate_header(header) is None  # frozen bundle publishes its hash
    assert header["reference_bundle"]["sha256"] == bundle["sha256"]


def test_reference_bundle_checksum_freezes_content(tmp_path: Path):
    bundle_path = tmp_path / "evaluator_v4_references.json"
    content = {"Q012": {"conclusions": ["the licence is refused", "suspension follows refusal"]}}
    bundle_path.write_text(json.dumps(content), encoding="utf-8")

    first = reference_bundle_checksum(bundle_path)
    assert first["status"] == "frozen"
    assert first["sha256"] == hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    # Any content change (in-place edit attempt) is visible — R2.
    bundle_path.write_text(json.dumps(content) + "\n", encoding="utf-8")
    second = reference_bundle_checksum(bundle_path)
    assert second["sha256"] != first["sha256"]

    header = publication_header(reference_bundle=bundle_path)
    assert header["reference_bundle"]["sha256"] == second["sha256"]
    header["boot_rules"] = list(BOOT_RULES)
    validate_header(header)  # frozen bundle must publish its hash


def test_format_publication_header_prints_version_and_checksum():
    bundle_path = Path(__file__).with_name("unused.json")
    header = publication_header(thresholds={"provision_identity": 0.6}, reference_bundle=bundle_path)
    text = format_publication_header(header)
    assert RULER_VERSION in text
    assert "ABSENT" in text  # no hash echo without a real bundle
    assert "provision_identity=0.6" in text
    assert all(rule in text for rule in BOOT_RULES)


@pytest.mark.parametrize(
    "mutator",
    [
        {"del": "boot_rules"},
        {"del": "reference_bundle"},
        {"del": "thresholds"},
        {"set": ("boot_rules", [])},
        {"set": ("ruler_id", "someone-elses-ruler")},
        {"set": ("reference_bundle", {"status": "frozen", "sha256": None})},
        {"set": ("ruler_version", "4.0")},  # must match ^v
    ],
)
def test_validate_header_rejects_incomplete_claims(mutator):
    header = publication_header()
    action, payload = next(iter(mutator.items()))
    if action == "del":
        header.pop(payload)
    else:
        header[payload[0]] = payload[1]
    with pytest.raises(RulerPublicationError):
        validate_header(header)


def test_write_json_with_header_embeds_header_in_artifact(tmp_path: Path):
    out_path = tmp_path / "sub" / "ruler_report.json"
    returned = write_json_with_header(out_path, {"n_scored": 150, "evaluator_miss_n": 57})
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["publication_header"] == returned
    assert on_disk["n_scored"] == 150
    assert REQUIRED_BINARY_KEYS == ("credit_binary", "claim_binary")


def test_write_json_with_header_returns_header_echoed(tmp_path: Path, capsys):
    out_path = tmp_path / "ruler_report.json"
    header = write_json_with_header(out_path, {"records": 1})
    printed = capsys.readouterr().out
    assert RULER_VERSION in printed
    assert all(rule in printed for rule in header["boot_rules"])
