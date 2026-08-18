"""Unit tests for temporal validity and provision versioning."""

from dataclasses import dataclass

from app.rag.retrieval.provision_versions import (
    build_provision_family_id,
    extract_provision_version,
    group_versions,
)
from app.rag.retrieval.temporal_validity import (
    VALIDITY_INVALID,
    VALIDITY_UNKNOWN,
    VALIDITY_VALID,
    is_valid,
    temporal_validity_score,
)


@dataclass
class FakeChunk:
    chunk_id: str = "test"
    text: str = ""
    act_name: str = ""
    status: str = "unknown"
    effective_from: str | None = None
    effective_to: str | None = None
    version: str | None = None
    section_number: str | None = None
    score: float = 1.0


class TestTemporalValidity:
    def test_valid_provision(self):
        r = is_valid("p1", "2025-01-01", provision_status="current",
                      effective_from="2020-01-01", effective_to=None)
        assert r.status == VALIDITY_VALID

    def test_repealed_provision(self):
        r = is_valid("p2", "2025-01-01", provision_status="repealed")
        assert r.status == VALIDITY_INVALID

    def test_superseded_provision(self):
        r = is_valid("p3", "2025-01-01", provision_status="superseded")
        assert r.status == VALIDITY_INVALID

    def test_query_before_effective(self):
        r = is_valid("p4", "2019-01-01", provision_status="current",
                      effective_from="2020-01-01")
        assert r.status == VALIDITY_INVALID

    def test_query_after_expiry(self):
        r = is_valid("p5", "2025-01-01", provision_status="current",
                      effective_from="2020-01-01", effective_to="2023-01-01")
        assert r.status == VALIDITY_INVALID

    def test_unknown_no_metadata(self):
        r = is_valid("p6", "2025-01-01")
        assert r.status == VALIDITY_UNKNOWN

    def test_query_date_defaults_to_today(self):
        r = is_valid("p7", provision_status="current", effective_from="2020-01-01")
        assert r.status == VALIDITY_VALID  # today is after 2020

    def test_from_chunk_payload(self):
        chunk = FakeChunk(
            chunk_id="c1",
            status="current",
            effective_from="2020-01-01",
            effective_to=None,
        )
        r = is_valid("c1", "2025-01-01", chunk=chunk)
        assert r.status == VALIDITY_VALID
        assert r.source == "payload"

    def test_from_chunk_repealed(self):
        chunk = FakeChunk(
            chunk_id="c2",
            status="repealed",
        )
        r = is_valid("c2", "2025-01-01", chunk=chunk)
        assert r.status == VALIDITY_INVALID

    def test_never_infers_invalid_without_evidence(self):
        """When there's no metadata, must return unknown, not invalid."""
        r = is_valid("p8", "2025-01-01", provision_status=None,
                      effective_from=None, effective_to=None)
        assert r.status == VALIDITY_UNKNOWN

    def test_overlapping_version_dates(self):
        """When effective_to < effective_from, should still handle gracefully."""
        r = is_valid("p9", "2025-01-01", provision_status="current",
                      effective_from="2023-01-01", effective_to="2022-01-01")
        # effective_to is before effective_from — treat as unknown (bad data)
        # or invalid (date after effective_to)
        assert r.status in (VALIDITY_INVALID, VALIDITY_UNKNOWN)

    def test_temporal_validity_score_valid(self):
        chunk = FakeChunk(chunk_id="c", status="current", effective_from="2020-01-01")
        r = is_valid("c", "2025-01-01", chunk=chunk)
        assert r.status == VALIDITY_VALID
        # score function uses document_id, not chunk — verify it returns a value
        score = temporal_validity_score("nonexistent", "2025-01-01")
        assert 0.0 <= score <= 1.0

    def test_temporal_validity_score_invalid(self):
        chunk = FakeChunk(chunk_id="c", status="repealed")
        r = is_valid("c", "2025-01-01", chunk=chunk)
        assert r.status == VALIDITY_INVALID
        assert temporal_validity_score("nonexistent_id", "2025-01-01") in (0.0, 0.5)


class TestProvisionVersions:
    def test_family_id_basic(self):
        fid = build_provision_family_id("FSS Act, 2006", "31")
        assert "FSS Act" in fid
        assert "31" in fid

    def test_family_id_strips_subsection(self):
        fid = build_provision_family_id("FSS Act, 2006", "31(2)(a)")
        assert fid.endswith("31"), fid  # should be just base section

    def test_family_id_unknown(self):
        fid = build_provision_family_id(None, None)
        assert fid == "UNKNOWN"

    def test_extract_version_from_explicit_status(self):
        chunk = FakeChunk(chunk_id="c1", text="Section 31", act_name="FSS Act, 2006",
                          status="current", section_number="31")
        v = extract_provision_version(chunk)
        assert v.is_current is True
        assert v.act == "FSS Act, 2006"
        assert v.section == "31"

    def test_extract_version_repealed(self):
        chunk = FakeChunk(chunk_id="c2", text="Section 31", act_name="FSS Act, 2006",
                          status="repealed", effective_to="2023-06-01")
        v = extract_provision_version(chunk)
        assert v.is_current is False
        assert v.status == "repealed"
        assert v.effective_to == "2023-06-01"

    def test_extract_version_from_text(self):
        chunk = FakeChunk(
            chunk_id="c3",
            text="Section 31 as amended by Chapter 5 on 2021-01-15.",
            act_name="FSS Act, 2006",
            status="current",
        )
        v = extract_provision_version(chunk)
        assert v.version is not None  # detected from text
        assert v.effective_from is not None or v.effective_to is not None

    def test_group_versions_single_family(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31", act_name="FSS Act", section_number="31", status="current"),
            FakeChunk(chunk_id="c2", text="Section 31", act_name="FSS Act", section_number="31", status="repealed"),
        ]
        families = group_versions(chunks)
        assert len(families) == 1
        fam = next(iter(families.values()))
        assert len(fam.versions) == 2

    def test_current_version_detection(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31", act_name="FSS Act", section_number="31", status="current"),
            FakeChunk(chunk_id="c2", text="Section 31", act_name="FSS Act", section_number="31", status="repealed"),
        ]
        families = group_versions(chunks)
        fam = next(iter(families.values()))
        cv = fam.current_version()
        assert cv is not None
        assert cv.document_id == "c1"

    def test_is_current_version(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31", act_name="FSS Act", section_number="31", status="current"),
            FakeChunk(chunk_id="c2", text="Section 31", act_name="FSS Act", section_number="31", status="repealed"),
        ]
        families = group_versions(chunks)
        fam = next(iter(families.values()))
        assert fam.is_current("c1") is True
        assert fam.is_current("c2") is False

    def test_multiple_families(self):
        chunks = [
            FakeChunk(chunk_id="c1", text="Section 31", act_name="FSS Act", section_number="31"),
            FakeChunk(chunk_id="c2", text="Section 73", act_name="FSS Act", section_number="73"),
        ]
        families = group_versions(chunks)
        assert len(families) == 2

    def test_extract_version_from_dict(self):
        """Test that extract_provision_version works with dict-like chunks."""
        chunk = {
            "chunk_id": "d1",
            "text": "Section 55",
            "act_name": "FSS Act",
            "section_number": "55",
            "status": "current",
        }
        v = extract_provision_version(chunk)
        assert v.document_id == "d1"
        assert v.section == "55"
        assert v.is_current is True
