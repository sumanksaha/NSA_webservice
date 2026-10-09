"""Tests for ``scripts/corpus_gap_check.py`` (manifest vs. live Qdrant).

Fully offline: :func:`_qdrant_client` is monkeypatched with a fake whose
``scroll`` returns a canned in-memory payload index, so no cluster, network or
``RAG_QDRANT_URL`` is required.

The two bugs these tests exist to pin down:

1. ``--domain`` restricted only the *collections scrolled*, not the *rows
   evaluated* — so every out-of-scope document was reported as "missing".
2. stale-note detection sat in an ``elif`` chain that only ran when the
   document HAD points, inverting its own definition and reporting ingested
   documents as stale.
"""

from __future__ import annotations

import json

import pytest

import scripts.corpus_gap_check as gap

# --------------------------------------------------------------------------- #
# Fake Qdrant
# --------------------------------------------------------------------------- #


class FakeRecord:
    def __init__(self, payload):
        self.payload = payload


class FakeClient:
    """Serves ``{collection: {document_id: n_points}}`` as scroll pages."""

    def __init__(self, index: dict[str, dict[str, int]]):
        self.index = index
        self.scrolled: list[str] = []

    def collection_exists(self, collection_name):
        return collection_name in self.index

    def scroll(self, collection_name, limit=1000, with_payload=True, with_vectors=False, offset=None):
        self.scrolled.append(collection_name)
        counts = self.index.get(collection_name, {})
        # One record per point keeps the count exact without real paging.
        records = [FakeRecord({"document_id": did}) for did, n in counts.items() for _ in range(n)]
        return records, None


@pytest.fixture
def client_factory(monkeypatch):
    """Install a fake client; returns a setter taking the index mapping."""

    def install(index):
        client = FakeClient(index)
        monkeypatch.setattr(gap, "_qdrant_client", lambda: client)
        return client

    return install


def _doc(document_id, domain, **extra):
    row = {"file": f"{document_id}.pdf", "document_id": document_id, "domain": domain}
    row.update(extra)
    return row


#: Baseline index: everything ingested EXCEPT the pending scanned doc ``env_b``.
ALL_BUT_ENV_B = {
    "env_legal_768": {"env_a": 5},
    "commercial_legal_768": {"comm_a": 3},
}

#: Everything ingested, including the scanned ``env_b``.
ALL_INGESTED = {
    "env_legal_768": {"env_a": 5, "env_b": 2},
    "commercial_legal_768": {"comm_a": 3},
}


@pytest.fixture
def corpus(tmp_path):
    for name in ("env_a.pdf", "env_b.pdf", "env_dup.pdf", "comm_a.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4")
    manifest = {
        "documents": [
            _doc("env_a", "env", notes="Readable text."),
            _doc("env_b", "env", requires_ocr=True, notes="SCANNED — 0 selectable text"),
            _doc("env_dup", "env", ingest=False, notes="byte-identical duplicate"),
            _doc("comm_a", "commercial", notes="Readable text."),
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Core reporting
# --------------------------------------------------------------------------- #


def test_reports_only_genuinely_missing_documents(corpus, client_factory):
    client_factory({"env_legal_768": {"env_a": 5}, "commercial_legal_768": {"comm_a": 3}})
    report = gap.build_report(corpus)

    assert report["totals"]["missing"] == 1
    assert report["totals"]["ingested"] == 2
    assert report["totals"]["skipped"] == 1
    assert [r["document_id"] for r in report["findings"]["missing"]] == ["env_b"]
    assert report["findings"]["missing"][0]["requires_ocr"] is True


def test_ingest_false_is_skipped_not_missing(corpus, client_factory):
    client_factory({"env_legal_768": {"env_a": 5}})
    report = gap.build_report(corpus)
    assert [r["document_id"] for r in report["findings"]["skipped"]] == ["env_dup"]
    assert "env_dup" not in [r["document_id"] for r in report["findings"]["missing"]]


def test_missing_collection_reports_every_document_missing(corpus, client_factory):
    """A domain whose collection does not exist yet is all-missing, not a crash."""
    client_factory({"env_legal_768": {"env_a": 5}})
    report = gap.build_report(corpus)
    missing = {r["document_id"] for r in report["findings"]["missing"]}
    assert "comm_a" in missing


# --------------------------------------------------------------------------- #
# Regression: --domain must filter rows, not just collections
# --------------------------------------------------------------------------- #


def test_domain_filter_excludes_other_domains_from_totals(corpus, client_factory):
    client = client_factory({"env_legal_768": {"env_a": 5}, "commercial_legal_768": {"comm_a": 3}})
    report = gap.build_report(corpus, domains=["env"])

    assert report["totals"]["manifest_entries"] == 3  # env rows only
    assert report["totals"]["missing"] == 1  # env_b only — NOT comm_a
    assert [r["document_id"] for r in report["findings"]["missing"]] == ["env_b"]
    # only the selected collection is scrolled
    assert "commercial_legal_768" not in client.scrolled


def test_domain_filter_with_clean_domain_reports_zero_missing(corpus, client_factory):
    client_factory({"env_legal_768": {"env_a": 5, "env_b": 2}, "commercial_legal_768": {"comm_a": 3}})
    report = gap.build_report(corpus, domains=["commercial"])
    assert report["totals"]["missing"] == 0
    assert report["totals"]["ingested"] == 1


# --------------------------------------------------------------------------- #
# Regression: stale notes mean "claims done, has 0 points"
# --------------------------------------------------------------------------- #


def test_stale_note_flagged_when_note_claims_ingest_but_no_points(corpus, client_factory):
    manifest = json.loads(corpus.read_text(encoding="utf-8"))
    manifest["documents"][1]["notes"] = "SCANNED — EasyOCR applied at ingest."
    corpus.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    client_factory({"env_legal_768": {"env_a": 5}})
    report = gap.build_report(corpus)

    assert [r["document_id"] for r in report["findings"]["stale_notes"]] == ["env_b"]
    assert report["totals"]["stale_notes"] == 1


def test_ingested_document_with_matching_note_is_not_stale(corpus, client_factory):
    """The inverted-branch bug: a real ingest claim on a real ingest is fine."""
    manifest = json.loads(corpus.read_text(encoding="utf-8"))
    manifest["documents"][0]["notes"] = "EasyOCR applied at ingest."
    corpus.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    client_factory(ALL_BUT_ENV_B)
    report = gap.build_report(corpus)

    assert report["findings"]["stale_notes"] == []
    assert report["totals"]["missing"] == 1  # env_b still pending, just not claimed


def test_benign_notes_never_match_the_claim_regex():
    assert gap._INGEST_CLAIM_RE.search("Readable text.") is None
    assert gap._INGEST_CLAIM_RE.search("Superseded by the 2022 amendments (kept as history)") is None
    assert gap._INGEST_CLAIM_RE.search("PENDING: NOT yet OCR'd or ingested") is None
    assert gap._INGEST_CLAIM_RE.search("EasyOCR applied at ingest") is not None


# --------------------------------------------------------------------------- #
# Orphans / absent files
# --------------------------------------------------------------------------- #


def test_orphan_document_in_qdrant_is_reported(corpus, client_factory):
    client_factory({"env_legal_768": {"env_a": 5, "ghost_doc": 2}})
    report = gap.build_report(corpus)
    assert [r["document_id"] for r in report["findings"]["orphan"]] == ["ghost_doc"]


def test_absent_file_flagged_for_indexed_document(corpus, client_factory):
    """Indexed, but the source PDF is gone — reported separately from missing."""
    manifest = json.loads(corpus.read_text(encoding="utf-8"))
    manifest["documents"][0]["file"] = "vanished.pdf"
    corpus.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    client_factory(ALL_INGESTED)
    report = gap.build_report(corpus)

    assert report["findings"]["missing"] == []
    assert [r["document_id"] for r in report["findings"]["absent_file"]] == ["env_a"]


# --------------------------------------------------------------------------- #
# CLI contract
# --------------------------------------------------------------------------- #


def test_exit_code_1_when_gaps_else_0(corpus, client_factory, monkeypatch, capsys):
    client_factory(ALL_INGESTED)
    assert gap.main(["--manifest", str(corpus)]) == 0

    client_factory(ALL_BUT_ENV_B)
    assert gap.main(["--manifest", str(corpus)]) == 1


def test_main_writes_json_report(corpus, client_factory, tmp_path):
    client_factory(ALL_BUT_ENV_B)
    out = tmp_path / "reports" / "gap.json"
    assert gap.main(["--manifest", str(corpus), "--out", str(out)]) == 1
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["mode"] == "READ_ONLY"
    assert saved["totals"]["missing"] == 1


def test_main_exit_2_on_missing_manifest(tmp_path):
    assert gap.main(["--manifest", str(tmp_path / "nope.json")]) == 2


def test_main_exit_2_without_qdrant_url(corpus, monkeypatch):
    monkeypatch.delenv("RAG_QDRANT_URL", raising=False)

    def boom():
        raise RuntimeError("RAG_QDRANT_URL not set — cannot reach Qdrant")

    monkeypatch.setattr(gap, "_qdrant_client", boom)
    assert gap.main(["--manifest", str(corpus)]) == 2
