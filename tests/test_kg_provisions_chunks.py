"""Tests for the KG → prompt-context bridge (2026-08-12 follow-up).

Covers:
* ``kg.hybrid.provisions_to_retrieved_chunks`` — converting KG expansion
  provisions into ``RetrievedChunk`` objects for the LLM context.
* ``kg.queries.provisions_for_query`` — the fast retrieval path (concept
  traversal + full-text fallback) used by ARM D and the production wiring.

These are pure-function tests (no Neo4j / Qdrant / LLM required).
"""

from __future__ import annotations

from kg.hybrid import provisions_to_retrieved_chunks
from kg.queries import provisions_for_query


def _prov(
    pid: str,
    number: str,
    title: str = "Provision title",
    instrument: str = "Food Safety and Standards Act, 2006",
    status: str = "current",
    domain: str = "FOOD_SAFETY",
    authorities: list[str] | None = None,
    text: str = "The provision text.",
) -> dict:
    return {
        "provision_id": pid,
        "provision_number": number,
        "title": title,
        "text": text,
        "instrument_title": instrument,
        "status": status,
        "legal_domain": domain,
        "authorities": authorities or [],
        "issuing_authority": "",
    }


# --------------------------------------------------------------------------- #
# provisions_to_retrieved_chunks
# --------------------------------------------------------------------------- #
def test_converts_provisions_to_chunks():
    provs = [
        _prov("FSS:SEC_16", "16", "Duties of the Authority"),
        _prov("FSS:SEC_32", "32", "Improvement notices"),
    ]
    chunks = provisions_to_retrieved_chunks(provs, limit=5)

    assert len(chunks) == 2
    assert chunks[0].chunk_id == "KG:FSS:SEC_16"
    assert chunks[0].section_number == "16"
    assert chunks[0].document_type == "KG-Provision"
    assert chunks[0].document_title == "Food Safety and Standards Act, 2006"
    assert "Duties of the Authority" in chunks[0].text
    assert "Instrument: Food Safety and Standards Act, 2006" in chunks[0].text
    assert "Status: current" in chunks[0].text


def test_limit_and_empty():
    provs = [_prov(f"P{i}", str(i)) for i in range(7)]
    assert len(provisions_to_retrieved_chunks(provs, limit=3)) == 3
    assert len(provisions_to_retrieved_chunks(provs, limit=20)) == 7
    assert provisions_to_retrieved_chunks([]) == []


def test_scores_rank_below_retrieved():
    """KG chunks must sort below any real (non-negative) retrieval score."""
    chunks = provisions_to_retrieved_chunks([_prov("P1", "1")], limit=5)
    assert all(c.score < 0.0 for c in chunks)
    # descending order keeps real chunks first
    assert sorted(chunks, key=lambda c: c.score, reverse=True) == chunks


def test_includes_authorities_and_domain():
    prov = _prov("P1", "3", authorities=["State Commissioner"], domain="ANIMAL_SLAUGHTER")
    chunks = provisions_to_retrieved_chunks([prov], limit=5)
    assert "Authority: State Commissioner" in chunks[0].text
    assert "Domain: ANIMAL_SLAUGHTER" in chunks[0].text


# --------------------------------------------------------------------------- #
# provisions_for_query (fast retrieval path)
# --------------------------------------------------------------------------- #
class _FakeQueries:
    """Minimal stand-in for LegalKGQueries exercising the concept/fallback logic."""

    def __init__(self, cross: list | None = None, search: list | None = None):
        self.cross = cross or []
        self.search = search or []
        self.concept_calls = 0

    def get_cross_domain_laws(self, concept: str) -> list:
        self.concept_calls += 1
        return self.cross

    def search_provisions(self, query: str, domain: str | None = None, limit: int = 10) -> list:
        return self.search[:limit]


def test_provisions_for_query_uses_concept_traversal():
    q = _FakeQueries(
        cross=[_prov("WBMO:SEC_3", "3", instrument="West Bengal Meat Order, 1965")]
    )
    out = provisions_for_query("What slaughterhouse rules apply?", q, limit=5)
    assert out and out[0]["provision_id"] == "WBMO:SEC_3"
    assert q.concept_calls >= 1


def test_provisions_for_query_falls_back_to_fulltext():
    q = _FakeQueries(
        cross=[],
        search=[_prov("FSS:SEC_22", "22", "Penalties", instrument="FSS Act, 2006")],
    )
    out = provisions_for_query("complete gibberish zzz", q, limit=10)
    assert out and out[0]["provision_id"] == "FSS:SEC_22"
    assert len(out) == 1


def test_provisions_for_query_limit():
    q = _FakeQueries(
        cross=[_prov(f"P{i}", str(i)) for i in range(12)]
    )
    out = provisions_for_query("slaughter", q, limit=4)
    assert len(out) == 4


# End of test_kg_provisions_chunks.py
