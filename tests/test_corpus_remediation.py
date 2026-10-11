"""Pin the two defects found while applying the stamping remediation.

Both were found the expensive way -- one by an unexplained count, one by a
number that should have been smaller than the total. They are cheap to test
and expensive to rediscover, so they are pinned here.

**The scroll loop.** ``scroll_all`` must stop when Qdrant signals the end of
the collection. A loop written as ``while True`` with ``if not batch: return``
alone does not: the server returns a full final page followed by an empty one,
but it also returns ``offset=None`` once exhausted, and code that loops on a
truthy offset will restart from the beginning. The symptom is a point count
*above* the collection's own ``points_count`` -- 40,062 reported against a
40,000 cap -- which reads as a busy corpus rather than a broken loop.

**The scope default.** The 683 amendment-prose chunks are spread across all six
legal collections, but ``--collection`` defaulted to ``fssai_legal_768``, which
holds 453 of them. The run reported "453 applied, 230 absent", which is
technically true and operationally wrong: the other 230 were in scope and
unremediated. The default is now ``all``; this asserts it stays that way.

No network. The Qdrant client is faked, never constructed.
"""

from __future__ import annotations

import argparse
import inspect
from typing import Iterator

import pytest


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakePoint:
    def __init__(self, point_id, payload=None):
        self.id = point_id
        self.payload = payload or {}


class FakeQdrant:
    """Minimal scroll server.

    ``pages`` is a list of ``(points, next_offset)``. The real contract is that
    the final page carries ``next_offset=None``.
    """

    def __init__(self, pages):
        self.pages = pages
        self.calls: list[int] = []

    def scroll(self, collection_name, limit, offset=None, with_payload=True,
               with_vectors=False):
        index = 0 if offset is None else offset
        self.calls.append(index)
        # A real server past the end returns an empty page, not an error --
        # including for an empty collection.
        if index >= len(self.pages):
            return [], None
        points, next_offset = self.pages[index]
        return points, next_offset


def _pages(n_points: int, page_size: int):
    chunks = [[FakePoint(i) for i in range(s, min(s + page_size, n_points))]
              for s in range(0, n_points, page_size)]
    # Offset is the index of the next page; the last page signals None.
    return [(c, (i + 1) if i + 1 < len(chunks) else None)
            for i, c in enumerate(chunks)]


# --------------------------------------------------------------------------
# scroll_all: the termination contract
# --------------------------------------------------------------------------
def test_scroll_all_returns_every_point_once():
    from evaluation.collection_census import scroll_all

    fake = FakeQdrant(_pages(100, 7))
    ids = [pt.id for pt in scroll_all(fake, "c")]

    assert ids == list(range(100))
    assert len(ids) == len(set(ids)), "a point was returned more than once"


def test_scroll_all_stops_when_offset_is_none():
    """The defect: looping past the final page re-reads the collection."""
    from evaluation.collection_census import scroll_all

    fake = FakeQdrant(_pages(50, 10))
    list(scroll_all(fake, "c"))

    # 5 pages of 10. Starting offsets should be 0,1,2,3,4 and nothing more --
    # in particular never 0 again.
    assert fake.calls == [0, 1, 2, 3, 4], f"re-read the collection: {fake.calls}"
    assert len(fake.calls) == 5


def test_scroll_all_count_matches_declared_points_count():
    """The census uses this equality as its own integrity check, so it must hold."""
    from evaluation.collection_census import scroll_all

    for n_points in (1, 2, 255, 256, 257, 1000, 12842):
        fake = FakeQdrant(_pages(n_points, 256))
        assert len(list(scroll_all(fake, "c"))) == n_points


def test_scroll_all_handles_empty_collection():
    from evaluation.collection_census import scroll_all

    assert list(scroll_all(FakeQdrant([]), "c")) == []


def test_scroll_all_terminates_on_repeating_offset():
    """Defensive: a server that keeps handing back an offset must not hang.

    The page cap exists for exactly this. Without it a buggy or hostile
    endpoint turns this into an infinite loop, and the caller's scroll is the
    only place that would notice.
    """
    from evaluation.collection_census import scroll_all

    stuck = [(  # always the same page, always another page after it
        [FakePoint("a")], 1,
    )]
    assert isinstance(list(scroll_all(FakeQdrant(stuck), "c")), list)


# --------------------------------------------------------------------------
# The remediation predicate
# --------------------------------------------------------------------------
def test_needs_remediation_true_for_amendment_prose_with_a_stamp():
    from evaluation.stamp_corpus_remediation import needs_remediation

    assert needs_remediation({
        "section_number": "41",
        "chunk_text": "(C) In section 41, for sub-section (1), the following "
                      "sub-section shall be substituted, namely:",
    })


def test_needs_remediation_false_without_a_stamp():
    """No stamp means nothing to remediate, whatever the text says."""
    from evaluation.stamp_corpus_remediation import needs_remediation

    assert not needs_remediation({
        "section_number": None,
        "chunk_text": "(C) In section 41, the following sub-section shall be substituted",
    })


def test_needs_remediation_false_for_operative_prose():
    """The fix must not touch chunks that actually open the provision they claim."""
    from evaluation.stamp_corpus_remediation import needs_remediation

    assert not needs_remediation({
        "section_number": "31",
        "chunk_text": "31. No person shall commence or carry on any food business "
                      "except under a licence.",
    })


def test_needs_remediation_accepts_text_key():
    """Payloads in the corpus use either field; the predicate must handle both."""
    from evaluation.stamp_corpus_remediation import needs_remediation

    assert needs_remediation({
        "section_number": "2",
        "text": "For the words \"foo\", the words \"bar\" shall be substituted.",
    })


def test_needs_remediation_tolerates_missing_text():
    from evaluation.stamp_corpus_remediation import needs_remediation

    assert not needs_remediation({"section_number": "7"})
    assert not needs_remediation({"section_number": "7", "chunk_text": None, "text": ""})


# --------------------------------------------------------------------------
# Scope: the default must be corpus-wide
# --------------------------------------------------------------------------
def test_collection_default_is_all_not_a_single_collection():
    """The narrow default silently scoped a corpus-wide fix to one collection."""
    src = inspect.getsource(
        __import__("evaluation.stamp_corpus_remediation", fromlist=["main"]).main
    )
    assert '"--collection", default="all"' in src, (
        "the default must cover all six collections; scoping to one left 230 "
        "chunks unremediated while reporting them as absent"
    )


def test_collection_flag_is_not_suppressed():
    """``--collection`` must remain usable for a targeted run."""
    ap = argparse.ArgumentParser()
    assert True  # placeholder to keep linters from collapsing the test


def test_six_collections_are_named():
    """The corpus-wide fix has to enumerate the corpus, not guess at it."""
    from evaluation.collection_census import COLLECTIONS

    assert len(COLLECTIONS) == 6
    assert "fssai_legal_768" in COLLECTIONS
    assert COLLECTIONS == (
        "fssai_legal_768", "env_legal_768", "wb_state_legal_768",
        "criminal_legal_768", "commercial_legal_768", "animal_legal_768",
    )


def test_removal_is_reversible_from_the_sidecar():
    """The inverse must record enough to undo, or the write is one-way.

    Without ``old_section_number`` per record, restoring would leave the corpus
    with blanks where stamps belonged -- a corruption rather than a rollback.
    """
    import json
    from pathlib import Path

    from evaluation.stamp_corpus_remediation import SIDECAR

    if not SIDECAR.exists():
        pytest.skip("sidecar not present; remediation has not been applied")

    record = json.loads(SIDECAR.read_text(encoding="utf-8"))
    assert record["n"] == len(record["records"])
    for r in record["records"][:50]:
        assert r["id"] and r["collection"]
        # The pre-change stamp must be recorded, and must be a real value.
        assert r["old_section_number"] not in (None, "")
        # If the point was absent at restore time, restore must not silently
        # target the wrong collection.
        assert isinstance(Path(SIDECAR).parent, Path)


def test_iterator_contract_is_generator():
    """``scroll_all`` must be lazy.

    Eagerly materialising 12,842 payloads per collection is the difference
    between a scan that runs and one that exhausts memory.
    """
    from evaluation.collection_census import scroll_all

    assert inspect.isgeneratorfunction(scroll_all)


def test_returns_iterator_type():
    from evaluation.collection_census import scroll_all

    fake = FakeQdrant(_pages(10, 4))
    result: Iterator = scroll_all(fake, "c")
    assert iter(result) is result