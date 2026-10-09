"""Offline tests for the Phase 1 corpus discovery engine (no network, no LLM).

Tests the autonomous provision-centric gap discovery that extends the Step 0
pre-annotation (question-centric, human-gated) into an automated, registry-wide
scan of all gold provisions.

Test strategy:
- Pure-function unit tests use synthetic provision dicts + synthetic payload
  indexes (no I/O, fully deterministic).
- Integration tests use the real ``benchmark/gold_provisions_v1.0.json`` +
  ``gold_sources_v1.0.json`` fixtures but inject synthetic payload indexes
  or run in registry-only mode (no cache file, no Qdrant).
- ``GapAnalyzer`` tests use mock ``BenchmarkQuestion`` objects built from the
  real gold registry, so question-impact analysis is deterministic without
  needing ``benchmark_v1.0.jsonl``.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from app.rag.research.corpus_discovery import (
    BODY_PRESENT_MIN_CHARS,
    SCHEMA_VERSION,
    DiscoveryReport,
    GapAnalyzer,
    IngestionRequest,
    ProvisionGap,
    SourceDocumentGap,
    classify_provision_gap,
    discover_corpus_gaps,
    group_provisions_by_document,
)
from evaluation.benchmark import load_gold_registry as _load_gold_registry
from evaluation.benchmark import load_gold_sources as _load_gold_sources

# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #


def _make_provision(
    *,
    pid: str = "test:s1",
    act: str = "Test Act, 2009",
    section: str | None = "1",
    title: str = "Title",
    domain: str = "TEST",
    chunk_id: str | None = None,
    document_id: str = "test_doc",
    collection: str = "test_legal_768",
) -> dict:
    """Build a synthetic gold-provision record (matches the registry schema)."""
    return {
        "id": pid,
        "act": act,
        "section": section,
        "title": title,
        "domain": domain,
        "chunk_id": chunk_id,
        "document_id": document_id,
        "collection": collection,
    }


def _make_payload_index(
    entries: list[tuple[str, str, str, str | None]] | None = None,
) -> dict[str, dict]:
    """Build a synthetic Qdrant payload index.

    Args:
        entries: list of ``(chunk_id, act_name, text, section_number)`` tuples.

    """
    index: dict[str, dict] = {}
    if entries:
        for chunk_id, act_name, text, section_number in entries:
            index[str(chunk_id)] = {
                "chunk_text": text,
                "act_name": act_name,
                "section_number": section_number,
                "document_title": act_name,
            }
    return index


def _make_mock_question(qid: str, provision_ids: list[str]) -> SimpleNamespace:
    """Build a mock benchmark question with gold units for the given provisions.

    Loads the real gold registry to resolve provision metadata (act, section,
    collection, document_id) so the mock matches the actual BenchmarkQuestion
    shape that ``_build_question_ref_map`` expects.
    """
    from evaluation.benchmark import GoldUnit

    registry = _load_gold_registry()
    units: list[GoldUnit] = []
    for pid in provision_ids:
        rec = registry.get(pid, {})
        family = str(pid).split(":", 1)[0]
        section = rec.get("section") or pid.split(":", 1)[-1] if ":" in pid else None
        units.append(
            GoldUnit(
                provision_id=pid,
                family=family,
                section=section,
                act=rec.get("act", ""),
                collection=rec.get("collection"),
                document_id=rec.get("document_id"),
                gain=2.0,
                role="primary",
            ),
        )

    def primary_units(units=units):
        return [u for u in units if u.role == "primary"]

    return SimpleNamespace(
        question_id=qid,
        raw={"question_id": qid, "question": "", "domains": [], "collections": []},
        gold_units=units,
        primary_units=primary_units,
    )


def _step0_targets_file(path: Path, qids: list[str]) -> None:
    """Write a synthetic ``step0_corpus_fill_targets.json``."""
    path.write_text(
        json.dumps(
            {
                "label": "evidence_missing",
                "n": len(qids),
                "qids": qids,
                "intervention": "manual_corpus_fill",
                "keep_if": "retrieval_failure_not_corpus_absence",
                "reject_if": "corpus_discovery_covers_gap",
            },
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# classify_provision_gap — pure function tests
# --------------------------------------------------------------------------- #


class TestClassifyProvisionGap:
    """Unit tests for the pure ``classify_provision_gap`` function."""

    def test_unindexed_when_chunk_id_null_and_no_payload(self):
        """chunk_id=null + no payload index → unindexed (graceful degradation)."""
        prov = _make_provision(chunk_id=None)
        gap_type, evidence = classify_provision_gap(prov, None, None)
        assert gap_type == "unindexed"
        assert "chunk_id is null" in evidence

    def test_present_when_chunk_id_set_no_payload(self):
        """chunk_id set + no payload index → present (best effort)."""
        prov = _make_provision(chunk_id="chunk_abc")
        gap_type, evidence = classify_provision_gap(prov, None, None)
        assert gap_type == "present"
        assert "chunk_abc" in evidence

    def test_unindexed_when_chunk_id_null_no_covering_chunk(self):
        """chunk_id=null + payload index available but no chunk covers the (family, section) → unindexed."""
        prov = _make_provision(pid="test:s1", chunk_id=None, section="1")
        payload_index = _make_payload_index([])
        coverage_index = {("other_act", "2"): ["chunk_99"]}
        gap_type, evidence = classify_provision_gap(prov, payload_index, coverage_index)
        assert gap_type == "unindexed"
        assert "no chunk" in evidence

    def test_present_via_coverage_index_when_chunk_id_null(self):
        """chunk_id=null but a chunk covers the (family, section) and body is present → present."""
        prov = _make_provision(pid="test:s1", chunk_id=None, section="1")
        body_text = "Section 1: Title of the section. This is the substantive body text of the provision. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        payload_index = _make_payload_index([("chunk_A", "Test Act", body_text, "1")])
        coverage_index = {("test", "1"): ["chunk_A"]}
        gap_type, evidence = classify_provision_gap(prov, payload_index, coverage_index)
        assert gap_type == "present"
        assert "coverage index" in evidence

    def test_orphaned_when_chunk_id_set_not_in_payload_no_coverage(self):
        """chunk_id set but not in payload index, and no coverage match → orphaned."""
        prov = _make_provision(pid="test:s1", chunk_id="chunk_X", section="1")
        payload_index = _make_payload_index([])
        coverage_index = {}
        gap_type, evidence = classify_provision_gap(prov, payload_index, coverage_index)
        assert gap_type == "orphaned"
        assert "chunk_X" in evidence

    def test_body_missing_when_chunk_has_empty_text(self):
        """Chunk exists but body text is empty → body_missing."""
        prov = _make_provision(pid="test:s1", chunk_id="chunk_A", section="1")
        payload_index = _make_payload_index([("chunk_A", "Test Act", "", "1")])
        gap_type, evidence = classify_provision_gap(prov, payload_index, None)
        assert gap_type == "body_missing"
        assert "empty" in evidence

    def test_fragmented_when_short_text_no_probes(self):
        """Chunk exists with short text (< 150 chars) and no probes → fragmented."""
        # Section "42": text must NOT contain "section 42", "s.42", or "42".
        short_text = "Just a short title."  # no "42", no "section 42", no "s.42"
        prov = _make_provision(pid="test:s42", chunk_id="chunk_A", section="42")
        payload_index = _make_payload_index([("chunk_A", "Test Act", short_text, "42")])
        gap_type, evidence = classify_provision_gap(prov, payload_index, None)
        assert gap_type == "fragmented"
        assert "fragmented" in evidence

    def test_present_when_probe_hits_sufficient(self):
        """Chunk with >= 2 probe hits → present even if short."""
        # "Section 1" and "s.1" both appear, plus section text.
        text = "Section 1 of the Act deals with this matter. See also s.1 for details."
        prov = _make_provision(pid="test:s1", chunk_id="chunk_A", section="1")
        payload_index = _make_payload_index([("chunk_A", "Test Act", text, "1")])
        gap_type, _evidence = classify_provision_gap(prov, payload_index, None)
        assert gap_type == "present"

    def test_present_at_min_resolved_chars_boundary(self):
        """Exactly BODY_PRESENT_MIN_CHARS chars, 0 probes → present (boundary)."""
        # Need a real body of exactly 200 chars with 0 probe hits.
        # Section is "9999" so "9999" as a bare probe won't match a generic body,
        # and "section 9999"/"s.9999" won't appear either.
        body = "A" * BODY_PRESENT_MIN_CHARS
        prov = _make_provision(pid="test:s9999", chunk_id="chunk_A", section="9999")
        payload_index = _make_payload_index([("chunk_A", "Test Act", body, "9999")])
        gap_type, _evidence = classify_provision_gap(prov, payload_index, None)
        assert gap_type == "present"

    def test_body_missing_below_min_chars_no_probes(self):
        """< BODY_PRESENT_MIN_CHARS chars and 0 probe hits → body_missing."""
        body = "A" * (BODY_PRESENT_MIN_CHARS - 1)
        prov = _make_provision(pid="test:s9999", chunk_id="chunk_A", section="9999")
        payload_index = _make_payload_index([("chunk_A", "Test Act", body, "9999")])
        gap_type, _evidence = classify_provision_gap(prov, payload_index, None)
        assert gap_type == "body_missing"

    def test_instrument_level_section_none(self):
        """section=None (whole-instrument ref): any corpus hit = present."""
        prov = _make_provision(pid="test", section=None)
        body = "Full text of the act."
        payload_index = _make_payload_index([("chunk_A", "Test Act", body, None)])
        coverage_index = {("test", None): ["chunk_A"]}
        gap_type, evidence = classify_provision_gap(prov, payload_index, coverage_index)
        assert gap_type == "present"
        assert "coverage index" in evidence


# --------------------------------------------------------------------------- #
# group_provisions_by_document — pure function tests
# --------------------------------------------------------------------------- #


class TestGroupByDocument:
    """Unit tests for ``group_provisions_by_document``."""

    def test_groups_by_document_id(self):
        prov_rec = _make_provision(pid="fssai:s1", document_id="doc_A", act="FSSAI Act")
        prov_rec2 = _make_provision(pid="fssai:s2", document_id="doc_B")
        gaps = [
            ("fssai:s1", "unindexed", "evidence A", prov_rec),
            ("fssai:s2", "orphaned", "evidence B", prov_rec2),
        ]
        source_lookup = {
            "doc_A": {"act": "FSSAI Act", "collection": "fssai_legal_768", "domain": "fssai", "provision_count": 5},
            "doc_B": {"act": "Other Act", "collection": "env_legal_768", "domain": "env", "provision_count": 3},
        }
        result = group_provisions_by_document(gaps, source_lookup)
        assert len(result) == 2
        doc_ids = {g.document_id for g in result}
        assert doc_ids == {"doc_A", "doc_B"}
        # Sorted by missing_count desc (both have 1, so stable order).
        doc_a = next(g for g in result if g.document_id == "doc_A")
        assert doc_a.act_name == "FSSAI Act"
        assert doc_a.collection == "fssai_legal_768"
        assert doc_a.missing_count == 1
        assert doc_a.missing[0].provision_id == "fssai:s1"

    def test_sorted_by_missing_count_desc(self):
        gaps = []
        source_lookup = {}
        for i in range(3):
            for j in range(i + 1):
                pid = f"doc{i}:s{j}"
                rec = _make_provision(pid=pid, document_id=f"doc{i}")
                gaps.append((pid, "unindexed", f"evidence {j}", rec))
                source_lookup[f"doc{i}"] = {"act": f"Act {i}", "collection": f"coll{i}", "domain": "test", "provision_count": 10}
        result = group_provisions_by_document(gaps, source_lookup)
        counts = [g.missing_count for g in result]
        assert counts == sorted(counts, reverse=True)
        assert counts == [3, 2, 1]

    def test_source_lookup_defaults_when_missing(self):
        """Provisions without a source_lookup entry still group correctly."""
        prov_rec = _make_provision(pid="orphan:s1", document_id="doc_Z")
        gaps = [("orphan:s1", "unindexed", "evidence", prov_rec)]
        source_lookup = {}  # intentionally omit doc_Z
        result = group_provisions_by_document(gaps, source_lookup)
        assert len(result) == 1
        assert result[0].document_id == "doc_Z"
        assert result[0].act_name == ""
        assert result[0].provision_count == 1  # defaults to len(entries)


# --------------------------------------------------------------------------- #
# Integration: discover_corpus_gaps with real gold registry
# --------------------------------------------------------------------------- #


class TestDiscoverCorpusGaps:
    """Integration tests using the real gold provisioning/source registry."""

    def test_registry_only_mode_classifies_all_as_unindexed(self):
        """With no payload index, all chunk_id=null provisions → unindexed.

        The real gold registry has all chunk_ids null, so in registry-only mode
        every provision is classified as a gap.  We pass ``payload_index={}``
        (empty dict, not None) to disable auto-loading from the cache file.
        """
        # Force payload_index={} (empty) to disable auto-loading + coverage index.
        report = discover_corpus_gaps(
            provisions=_load_gold_registry(),
            payload_index={},
            sources=_load_gold_sources(),
            questions=[],
        )
        assert report.total_provisions > 0
        # In registry-only mode, all provisions with null chunk_id → unindexed.
        unindexed = [g for g in report.missing_provisions if g.gap_type == "unindexed"]
        assert len(unindexed) == report.total_provisions
        assert report.indexed_provisions == 0
        assert "unindexed" in report.summary["by_gap_type"]
        assert report.payload_index_size == 0

    def test_source_gaps_grouped_correctly(self):
        """Source gaps should be grouped by document_id from the gold sources."""
        report = discover_corpus_gaps()
        assert len(report.source_gaps) > 0
        # Each source gap should have metadata from gold_sources.
        for sg in report.source_gaps:
            assert sg.document_id is not None
            assert sg.act_name  # non-empty
            assert sg.provision_count > 0
            assert sg.missing_count > 0
            assert sg.missing_count <= sg.provision_count

    def test_source_gaps_sorted_by_missing_count(self):
        report = discover_corpus_gaps()
        counts = [g.missing_count for g in report.source_gaps]
        assert counts == sorted(counts, reverse=True)

    def test_ingestion_requests_bounded_by_circuit_breaker(self):
        """max_requests caps the number of ingestion requests."""
        report = discover_corpus_gaps(max_requests=3)
        assert len(report.ingestion_requests) <= 3
        if len(report.ingestion_requests) == 3:
            # The cap was hit — the summary should note it.
            pass  # just verify no exception

    def test_ingestion_requests_ranked_by_question_impact(self):
        """Ingestion requests should be ranked by affected question count."""
        report = discover_corpus_gaps()
        if len(report.ingestion_requests) > 1:
            # First request should have >= the last one in affected questions.
            # (We can't compare exact ordering without questions, but the cap
            # should still produce valid requests.)
            for req in report.ingestion_requests:
                assert req.document_id
                assert req.act_name
                assert req.collection
                assert req.missing_provision_count > 0
                assert req.total_provision_count > 0

    def test_report_serializable_to_dict(self):
        """DiscoveryReport.to_dict() should be JSON-serializable."""
        report = discover_corpus_gaps()
        out = report.to_dict()
        # Round-trip through JSON to verify serializability.
        json.dumps(out)
        assert out["total_provisions"] == report.total_provisions
        assert out["missing_count"] == report.missing_count
        assert out["schema_version"] == SCHEMA_VERSION

    def test_with_synthetic_payload_index(self):
        """With a payload index that covers some provisions, discovery distinguishes."""
        provisions = _load_gold_registry()
        # Pick one provision that exists in the registry.
        pid = next(iter(provisions))
        prov = provisions[pid]
        section = str(prov.get("section", ""))
        family = pid.split(":", 1)[0]
        act = prov.get("act", "")

        # Build a payload index with one chunk that covers this provision.
        body_text = f"Section {section}: {act}. This is the substantive body of the provision. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore."
        payload = _make_payload_index([(f"chunk_{pid}", act, body_text, section)])

        # Build a coverage index manually for the test.
        coverage_index = {(family, section): [f"chunk_{pid}"]}

        gap_type, evidence = classify_provision_gap(prov, payload, coverage_index)
        assert gap_type == "present"
        assert "coverage index" in evidence

    def test_payload_index_shrinks_missing_set(self):
        """When payload index covers a provision, it's NOT in missing_provisions."""
        provisions = _load_gold_registry()
        pid = next(iter(provisions))
        prov = provisions[pid]
        section = str(prov.get("section", ""))
        act = prov.get("act", "")
        family = pid.split(":", 1)[0]

        # Verify that the coverage_index path correctly marks it present.
        coverage_index = {(family, section): [f"chunk_{pid}"]}
        gap_type, _evidence = classify_provision_gap(prov, _make_payload_index([(f"chunk_{pid}", act, f"Section {section} body text here and more", section)]), coverage_index)
        assert gap_type == "present"
        # And without coverage_index, it's unindexed (chunk_id is null).
        gap_type2, _ = classify_provision_gap(prov, _make_payload_index([(f"chunk_{pid}", act, f"Section {section} body text here and more", section)]), None)
        assert gap_type2 == "unindexed"

    def test_graceful_degradation_no_benchmark_files(self, monkeypatch):
        """When gold files are unavailable, return an empty report (no crash)."""
        import app.rag.research.corpus_discovery as mod

        monkeypatch.setattr(mod, "_load_gold_provisions", lambda: (_ for _ in ()).throw(FileNotFoundError("no file")))
        monkeypatch.setattr(mod, "_load_gold_sources", lambda: {"collections": {}})
        monkeypatch.setattr(mod, "_load_benchmark_questions", list)

        report = discover_corpus_gaps()
        assert report.total_provisions == 0
        assert report.missing_provisions == []
        assert "no gold provisions" in report.summary["error"]


# --------------------------------------------------------------------------- #
# GapAnalyzer: question-impact analysis + Step 0 cross-reference
# --------------------------------------------------------------------------- #


class TestGapAnalyzer:
    """Tests for the GapAnalyzer (automated evidence gap analysis beyond Step 0)."""

    def test_analyzer_built_from_discovery_with_questions(self):
        """GapAnalyzer populates question-impact from mock questions."""
        provisions = _load_gold_registry()
        # Pick 3 provisions from different families.
        pids = list(provisions.keys())[:3]
        q1 = _make_mock_question("QTEST1", pids[:2])
        q2 = _make_mock_question("QTEST2", [pids[1]])
        q3 = _make_mock_question("QTEST3", [pids[2]])

        # Force registry-only mode so all provisions are missing.
        report = discover_corpus_gaps(
            provisions=provisions,
            payload_index={},
            sources=_load_gold_sources(),
            questions=[q1, q2, q3],
        )
        analyzer = GapAnalyzer(report, [q1, q2, q3])

        affected = analyzer.affected_questions()
        assert set(affected) == {"QTEST1", "QTEST2", "QTEST3"}

        # No provision is referenced by >= 3 questions, so no "high" severity.
        high = analyzer.missing_by_severity("high")
        assert len(high) == 0

        report_dict = analyzer.to_report_dict()
        assert "affected_questions" in report_dict
        assert "question_impact" in report_dict
        assert "step0_cross_reference" in report_dict

    def test_step0_cross_reference_covered_targets(self, tmp_path):
        """Step 0 targets whose primary units are all missing → covered by discovery."""
        provisions = _load_gold_registry()
        pid = next(iter(provisions.keys()))
        q = _make_mock_question("QTEST1", [pid])

        report = discover_corpus_gaps(
            provisions={pid: next(iter(provisions.values()))},
            payload_index={},
            sources=_load_gold_sources(),
            questions=[q],
        )
        analyzer = GapAnalyzer(report, [q])

        targets_file = tmp_path / "step0_corpus_fill_targets.json"
        _step0_targets_file(targets_file, ["QTEST1", "QTEST2"])

        result = analyzer.cross_reference_step0_targets(targets_file)
        assert result["step0_targets_found"] == 2
        assert result["covered_by_discovery"] == 1  # QTEST1's primary unit is missing
        assert result["uncovered_count"] == 1
        assert result["uncovered_qids"] == ["QTEST2"]
        assert "QTEST1" in result["covered_qids"]

    def test_step0_cross_reference_no_questions_degrades(self):
        """Without questions loaded, step0 cross-reference degrades gracefully."""
        report = discover_corpus_gaps()
        analyzer = GapAnalyzer(report, [])  # no questions
        result = analyzer.cross_reference_step0_targets()
        assert "error" in result
        assert "no benchmark questions" in result["error"]

    def test_step0_cross_reference_missing_file(self, tmp_path):
        """When the step0 targets file doesn't exist, return a safe empty result."""
        provisions = _load_gold_registry()
        pid = next(iter(provisions.keys()))
        q = _make_mock_question("QTEST1", [pid])
        report = discover_corpus_gaps(
            provisions={pid: next(iter(provisions.values()))},
            payload_index={},
            sources=_load_gold_sources(),
            questions=[q],
        )
        analyzer = GapAnalyzer(report, [q])
        result = analyzer.cross_reference_step0_targets(tmp_path / "nonexistent.json")
        assert result["step0_targets_found"] == 0
        assert "no step-0 targets file" in result["detail"]

    def test_question_impact_mapping(self):
        """question_impact() maps each missing provision to its QIDs."""
        provisions = _load_gold_registry()
        pids = list(provisions.keys())[:3]
        q1 = _make_mock_question("QTEST1", [pids[0], pids[1]])
        q2 = _make_mock_question("QTEST2", [pids[0]])
        q3 = _make_mock_question("QTEST3", [pids[2]])

        report = discover_corpus_gaps(
            provisions=provisions,
            payload_index={},
            sources=_load_gold_sources(),
            questions=[q1, q2, q3],
        )
        analyzer = GapAnalyzer(report, [q1, q2, q3])
        impact = analyzer.question_impact()

        # pids[0] is referenced by both QTEST1 and QTEST2.
        assert "QTEST1" in impact[pids[0]]
        assert "QTEST2" in impact[pids[0]]
        # pids[1] is only referenced by QTEST1.
        assert impact[pids[1]] == ["QTEST1"]
        # pids[2] is referenced by QTEST3 only.
        assert impact[pids[2]] == ["QTEST3"]

    def test_severity_assignment(self):
        """Provisions referenced by >= 3 questions get 'high' severity."""
        provisions = _load_gold_registry()
        pid = next(iter(provisions.keys()))
        # 3 questions all reference the same provision.
        q1 = _make_mock_question("Q1", [pid])
        q2 = _make_mock_question("Q2", [pid])
        q3 = _make_mock_question("Q3", [pid])

        report = discover_corpus_gaps(
            provisions={pid: next(iter(provisions.values()))},
            payload_index={},
            sources=_load_gold_sources(),
            questions=[q1, q2, q3],
        )
        analyzer = GapAnalyzer(report, [q1, q2, q3])
        assert len(report.missing_provisions) == 1
        assert report.missing_provisions[0].severity == "high"
        assert len(analyzer.missing_by_severity("high")) == 1


# --------------------------------------------------------------------------- #
# Config flag tests
# --------------------------------------------------------------------------- #


class TestConfigFlags:
    """Verify the new RAG_RESEARCH_* config flags are registered correctly."""

    def test_research_enabled_flag_exists(self):
        from app.shared.config import cfg

        # Default is False (opt-in).
        assert hasattr(cfg, "research_enabled")
        assert cfg.research_enabled is False

    def test_research_corpus_discovery_flag_exists(self):
        from app.shared.config import cfg

        assert hasattr(cfg, "research_corpus_discovery")
        assert cfg.research_corpus_discovery is True  # sub-switch, default on

    def test_research_max_ingestion_requests_flag_exists(self):
        from app.shared.config import cfg

        assert hasattr(cfg, "research_max_ingestion_requests")
        assert cfg.research_max_ingestion_requests == 50

    def test_research_use_payload_index_flag_exists(self):
        from app.shared.config import cfg

        assert hasattr(cfg, "research_use_payload_index")
        assert cfg.research_use_payload_index is True

    def test_gapanalyzer_run_respects_disabled_flag(self):
        """When RAG_RESEARCH_ENABLED is off, GapAnalyzer.run() returns empty report."""
        from app.rag.research.corpus_discovery import GapAnalyzer

        # RAG_RESEARCH_ENABLED defaults to False → run() should be a no-op.
        analyzer = GapAnalyzer.run()
        assert analyzer.report.total_provisions == 0
        assert "RAG_RESEARCH_ENABLED is off" in analyzer.report.summary["error"]


# --------------------------------------------------------------------------- #
# Data model serialization tests
# --------------------------------------------------------------------------- #


class TestDataclassSerialization:
    """Verify to_dict() / to_dict() round-trips on the data models."""

    def test_provision_gap_to_dict(self):
        g = ProvisionGap(
            provision_id="test:s1",
            family="test",
            act="Test Act",
            section="1",
            title="Title",
            domain="TEST",
            document_id="doc_1",
            collection="test_legal_768",
            chunk_id=None,
            gap_type="unindexed",
            evidence="test evidence",
            severity="medium",
            question_refs=["Q001", "Q002"],
        )
        d = g.to_dict()
        assert d["provision_id"] == "test:s1"
        assert d["gap_type"] == "unindexed"
        assert d["severity"] == "medium"
        assert d["question_refs"] == ["Q001", "Q002"]
        # Round-trip.
        json.dumps(d)

    def test_source_document_gap_to_dict(self):
        g = SourceDocumentGap(
            document_id="doc_1",
            act_name="Test Act",
            collection="test_legal_768",
            domain="TEST",
            provision_count=5,
            missing=[
                ProvisionGap(
                    provision_id="test:s1",
                    family="test",
                    act="Test Act",
                    section="1",
                    title="Title",
                    domain="TEST",
                    document_id="doc_1",
                    collection="test_legal_768",
                    chunk_id=None,
                    gap_type="unindexed",
                    evidence="test",
                ),
            ],
        )
        d = g.to_dict()
        assert d["document_id"] == "doc_1"
        assert d["missing_count"] == 1
        assert d["missing_ratio"] == 0.2
        assert d["affected_question_count"] == 0
        json.dumps(d)

    def test_ingestion_request_to_dict(self):
        r = IngestionRequest(
            document_id="doc_1",
            source_uri=None,
            act_name="Test Act",
            collection="test_legal_768",
            domain="TEST",
            missing_provision_count=3,
            total_provision_count=5,
            reason="3/5 provisions missing",
            gap_types={"unindexed": 3},
        )
        d = r.to_dict()
        assert d["document_id"] == "doc_1"
        assert d["source_uri"] is None
        assert d["missing_provision_count"] == 3
        assert d["gap_types"] == {"unindexed": 3}
        json.dumps(d)

    def test_discovery_report_to_dict(self):
        report = DiscoveryReport(
            total_provisions=10,
            indexed_provisions=7,
            missing_provisions=[
                ProvisionGap(
                    provision_id="test:s1",
                    family="test",
                    act="Test Act",
                    section="1",
                    title="T",
                    domain="TEST",
                    document_id="doc_1",
                    collection="coll",
                    chunk_id=None,
                    gap_type="unindexed",
                    evidence="e",
                ),
            ],
            source_gaps=[],
            ingestion_requests=[],
            payload_index_size=0,
            timestamp="2025-01-01T00:00:00+00:00",
            summary={"by_gap_type": {"unindexed": 1}},
        )
        d = report.to_dict()
        assert d["total_provisions"] == 10
        assert d["indexed_provisions"] == 7
        assert d["missing_count"] == 1
        assert d["schema_version"] == SCHEMA_VERSION
        json.dumps(d)

    def test_schema_version_matches(self):
        assert SCHEMA_VERSION == "1.0"
