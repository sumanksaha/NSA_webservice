"""Tests for the corpus-gap scope classifier (2026-10-05).

``corpus_gap_scope`` originally reported every unresolved gold unit as
``not_in_corpus``, which conflated two failures with opposite remedies:

* the instrument was never ingested  -> procurement;
* the instrument **is** indexed, but the chunk holding the provision has
  ``section_number=None`` -> re-chunk / backfill the section stamp.

The original P0-2 row prescribed procurement for the second case (the PCRA
Rules 2017 are already ingested, 1,100 chunks), so the distinction now has a
regression test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from evaluation.corpus_gap_scope import _untagged_evidence, norm


class _StubFamilyMap:
    """Minimal stand-in; ``_untagged_evidence`` only passes it through."""

    def family_s_for_act(self, act: str) -> list[str]:  # pragma: no cover - unused
        return ["pcra"]


def _payload(text: str, *, act: str = "The Prevention of Cruelty to Animals Act, 1960", **extra: Any) -> dict:
    return {
        "act_name": act,
        "document_title": "The Prevention of Cruelty to Animals (Rules) 2017",
        "section_number": None,
        "sections_covered": [],
        "chunk_text": text,
        **extra,
    }


def _registry(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, dict]) -> None:
    import evaluation.corpus_gap_scope as mod

    monkeypatch.setattr(mod, "load_gold_registry", lambda: mapping)


def test_detects_present_but_untagged_provision(monkeypatch: pytest.MonkeyPatch):
    """Text whose chunk opens with the section but carries no stamp."""
    _registry(monkeypatch, {"pcra:s4": {"section": "4"}})
    plist = [_payload("4 The owner of a breeding centre shall abide by the rules.")]

    out = _untagged_evidence({"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s4"}}, plist, _StubFamilyMap())

    assert "pcra:s4" in out, "an unstamped chunk containing the section should be detected"
    detail = out["pcra:s4"]
    assert detail["section"] == "4"
    assert detail["chunks_opening_with_section"] == 1
    assert "re-OCR" in detail["verdict"]


def test_stamped_chunk_is_not_reported_as_untagged(monkeypatch: pytest.MonkeyPatch):
    """A correctly stamped chunk would have resolved, so it is not a gap."""
    _registry(monkeypatch, {"pcra:s4": {"section": "4"}})
    plist = [_payload("4 The owner shall abide.", section_number="4")]

    out = _untagged_evidence({"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s4"}}, plist, _StubFamilyMap())

    assert "pcra:s4" not in out


def test_absent_text_is_not_reported_as_untagged(monkeypatch: pytest.MonkeyPatch):
    """Genuinely missing text must stay unclassified -> procurement path."""
    _registry(monkeypatch, {"pcra:s63": {"section": "63"}})
    plist = [_payload("12 Something entirely different.")]

    out = _untagged_evidence(
        {"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s63"}}, plist, _StubFamilyMap(),
    )

    assert "pcra:s63" not in out


def test_does_not_match_a_different_section_number(monkeypatch: pytest.MonkeyPatch):
    """Rule 4 must not be satisfied by a chunk opening with rule 40 or 4.1."""
    _registry(monkeypatch, {"pcra:s4": {"section": "4"}})
    plist = [_payload("40 Later provision entirely unrelated."), _payload("4.1 A sub-rule.")]

    out = _untagged_evidence({"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s4"}}, plist, _StubFamilyMap())

    assert "pcra:s4" not in out


def test_sections_covered_chunk_is_excluded(monkeypatch: pytest.MonkeyPatch):
    """``sections_covered`` already resolves, so it is not an untagged gap."""
    _registry(monkeypatch, {"pcra:s12": {"section": "12"}})
    plist = [_payload("12 Some text.", sections_covered=["12"])]

    out = _untagged_evidence(
        {"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s12"}}, plist, _StubFamilyMap(),
    )

    assert "pcra:s12" not in out


def test_norm_collapses_punctuation_and_case():
    assert norm("The Prevention of Cruelty to Animals Rules, 2017") == norm(
        "the prevention of cruelty to animals (rules) 2017",
    )


def test_unit_without_section_is_skipped(monkeypatch: pytest.MonkeyPatch):
    _registry(monkeypatch, {"pcra:all": {"section": None}})
    plist = [_payload("4 The owner shall abide.")]

    out = _untagged_evidence(
        {"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:all"}}, plist, _StubFamilyMap(),
    )

    assert "pcra:all" not in out


# --------------------------------------------------------------------------- #
# The real corpus must classify the three known PCRA gaps as untagged.
# Skipped when the payload cache is absent (it is gitignored).
# --------------------------------------------------------------------------- #


def test_real_corpus_classifies_pcra_gaps_as_present_but_untagged():
    from pathlib import Path

    cache = Path(__file__).resolve().parent.parent / "evaluation" / "out" / "cache" / "payload_index.jsonl"
    if not cache.exists():
        pytest.skip("payload cache absent; run the retrieval harness first")

    plist: list[dict[str, Any]] = []
    with open(cache, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                plist.append(json.loads(line)["payload"])

    out = _untagged_evidence(
        {"The Prevention of Cruelty to Animals Rules, 2017": {"pcra:s4", "pcra:s12", "pcra:s63"}},
        plist,
        _StubFamilyMap(),
    )
    # The three gaps are present-but-untagged, not absent. If this starts
    # failing after a re-ingest, the classification should be revisited.
    assert {"pcra:s4", "pcra:s12", "pcra:s63"} <= set(out)


# End of test_corpus_gap_scope.py
