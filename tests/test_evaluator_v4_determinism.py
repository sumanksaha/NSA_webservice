"""WF-8 determinism gate — two offline full passes of the v4 ruler.

The whole point of a ruler is reproducibility: the same frozen inputs
(reference bundle, answers, human tabulation, lexicon bans, calibration code)
must produce byte-identical outputs — every time, on every machine, with zero
LLM calls. G9 codified the failure mode this test kills.

Three levels are enforced:

1. ``rows`` — the armed component vector per answer builds identically twice.
2. the FULL published scorecard (header + calibration + confusion tables)
   written to two different paths must be byte-identical, embedded
   publication header included (the header is deterministic: version, bundle
   checksum, thresholds, boot rules — no wall-clock fields, by design).
3. the replicate discipline in the rail: a live-LLM arm requires 3 replicates
   and a paired gate (documented in ``evaluation.ruler.BOOT_RULES`` — rule 6),
   so offline determinism and online noise are governed by the same document.

Cached anti-caveat: determinism is proven by running the pipeline twice IN
PROCESS against the same frozen artifacts — cache hits are part of the design
(the bundle is checksummed; the cache keys are content-addressed), and the
redundant disk reads run inside the timed pass anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import evaluation.evaluator_v4 as ev
import evaluation.ruler as ruler


@pytest.fixture()
def scorecard_target(tmp_path, monkeypatch):
    """Point the published scorecard at a tmp path so live outputs stay stable."""
    monkeypatch.setattr(ev, "RESULTS", tmp_path / "scorecard.json")
    return tmp_path / "scorecard.json"


def test_two_full_row_builds_are_identical():
    rows_a = ev.build_rows()
    rows_b = ev.build_rows()
    assert len(rows_a) == len(rows_b) > 0
    for ra, rb in zip(rows_a, rows_b):
        assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True), ra["qid"]
    assert [r["qid"] for r in rows_a] == [r["qid"] for r in rows_b]


def test_full_scorecard_written_twice_is_byte_identical(scorecard_target):
    exit_a = ev.main()
    first = scorecard_target.read_bytes()
    exit_b = ev.main()
    second = scorecard_target.read_bytes()
    assert exit_a == exit_b == 0
    assert first == second, "the full published scorecard is not reproducible byte-for-byte"

    data = json.loads(first.decode("utf-8"))
    header = data["publication_header"]
    ruler.validate_header(header)  # raises on any lineage defect
    assert any("3 replicates" in rule for rule in header["boot_rules"]), (
        "the G9 replicate rule must ride inside every published header"
    )
    # and the header must equal a fresh one built right now (no clock decay)
    assert ruler.publication_header(thresholds=header["thresholds"]) == header
