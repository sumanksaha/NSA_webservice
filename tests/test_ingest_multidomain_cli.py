"""CLI tests for ``scripts/ingest_multidomain.py`` (Phase 2 — multi-domain).

Covers the pure helpers (Devanagari strip, manifest selection, meta mapping),
the dry-run path (real loader/cleaner/chunker on a tiny txt fixture — no
models, no Qdrant), exit-code semantics, reindex wiring, and per-domain
summary-file output. Real ingestion is monkeypatched so no Qdrant or
sentence-transformers is required.
"""

from __future__ import annotations

import json

import pytest

import scripts.ingest_multidomain as md

# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestStripDevanagari:
    def test_passthrough_when_no_devanagari(self):
        text = "The Commissioner may appoint inspectors.\n\nSection 33."
        assert md.strip_devanagari(text) == text

    def test_removes_devanagari_runs_in_mixed_lines(self):
        text = "Reg. No. \u0917\u0948\u091c\u0947\u091f \u0911\u092b \u0907\u0902\u0921\u093f\u092f\u093e Extraordinary"
        out = md.strip_devanagari(text)
        assert "\u0917" not in out
        assert "Reg. No." in out

    def test_blanks_mostly_hindi_lines_keeps_boundaries(self):
        text = "English preamble line.\n\u0915\u0947\u0902\u0926\u094d\u0930\u0940\u092f \u0938\u0930\u0915\u093e\u0930 \u0938\u0942\u091a\u0928\u093e \u092a\u094d\u0930\u0915\u093e\u0936\u0915\nSection 3."
        out = md.strip_devanagari(text)
        lines = out.splitlines()
        assert "preamble" in out
        assert "Section 3." in out
        # the Hindi-only middle line becomes a blank line (structure preserved)
        assert lines[1].strip() == ""

    def test_drops_devanagari_from_english_line(self):
        text = "Ministry of Environment \u092a\u0930\u094d\u092f\u093e\u0935\u0930\u0923 notification"
        out = md.strip_devanagari(text)
        assert "\u092a" not in out
        assert "Ministry of Environment" in out


class TestSelection:
    def _rows(self):
        return [
            {"file": "a.pdf", "domain": "env", "document_id": "a", "ingest": True},
            {"file": "b.pdf", "domain": "commercial", "document_id": "b", "ingest": False},
            {"file": "c.pdf", "domain": "env", "document_id": "c", "requires_ocr": True},
        ]

    def test_skips_ingest_false(self):
        rows = md.select_documents({"documents": self._rows()})
        assert [r["file"] for r in rows] == ["a.pdf", "c.pdf"]

    def test_domain_filter(self):
        rows = md.select_documents({"documents": self._rows()}, domain="commercial")
        assert rows == []

    def test_only_substring(self):
        rows = md.select_documents({"documents": self._rows()}, only="a.pdf")
        assert [r["file"] for r in rows] == ["a.pdf"]

    def test_skip_ocr(self):
        rows = md.select_documents({"documents": self._rows()}, skip_ocr=True)
        assert [r["file"] for r in rows] == ["a.pdf"]


class TestDocMeta:
    def test_maps_document_type_to_type(self):
        meta = md._doc_meta({"document_type": "act", "title": "X", "act_name": "A"})
        assert meta["type"] == "act"
        assert meta["title"] == "X"
        assert meta["act_name"] == "A"


# --------------------------------------------------------------------------- #
# CLI behaviours (no Qdrant, no models)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def corpus(tmp_path):
    """A 2-doc manifest: one clean English txt, one with Hindi header lines."""
    (tmp_path / "clean.txt").write_text(
        "The Environment (Protection) Act, 1986.\n\n"
        "Section 3. Power of Central Government to take measures.\n\n"
        "Section 5. Power to give directions.",
        encoding="utf-8",
    )
    (tmp_path / "hindi.txt").write_text(
        "Reg. No. \u0917\u0948\u091c\u0947\u091f \u0911\u092b \u0907\u0902\u0921\u093f\u092f\u093e\n"
        "Notification under the Act.\n\n"
        "\u0915\u0947\u0902\u0926\u094d\u0930\u0940\u092f \u0938\u0930\u0915\u093e\u0930 \u0938\u0942\u091a\u0928\u093e\n"
        "Section 8. Rules.",
        encoding="utf-8",
    )
    manifest = {
        "version": "0.1-draft",
        "collections": {"env": "env_legal_768"},
        "documents": [
            {
                "file": "clean.txt",
                "document_id": "clean_doc",
                "title": "Clean Doc",
                "document_type": "act",
                "authority": "Parliament",
                "jurisdiction": "India",
                "state": "",
                "domain": "env",
                "act_name": "Environment (Protection) Act, 1986",
                "is_current": True,
            },
            {
                "file": "hindi.txt",
                "document_id": "hindi_doc",
                "title": "Hindi Doc",
                "document_type": "notification",
                "authority": "MoEFCC",
                "jurisdiction": "India",
                "state": "",
                "domain": "env",
                "act_name": "Environment (Protection) Act, 1986",
                "is_current": True,
            },
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_dry_run_writes_summaries_and_counts(corpus, capsys):
    out_dir = corpus / "out"
    code = md.main(["--manifest", str(corpus / "manifest.json"), "--dry-run", "--out-dir", str(out_dir)])
    assert code == 0
    summary = json.loads((out_dir / "ingest_multidomain_summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "dry-run"
    assert summary["total_docs"] == 2
    assert summary["total_ok"] == 2
    assert summary["ok"] is True
    assert "env" in summary["domains"]
    assert summary["domains"]["env"]["collection"] == "env_legal_768"
    assert (out_dir / "ingest_multidomain_env.json").is_file()
    # the Hindi doc must yield fewer stripped chars than raw
    by_file = {r["file"]: r for r in summary["domains"]["env"]["results"]}
    assert by_file["hindi.txt"]["stripped_chars"] < by_file["hindi.txt"]["raw_chars"]
    assert by_file["hindi.txt"]["chunks"] >= 1
    assert by_file["clean.txt"]["chunks"] >= 1


def test_exit_2_when_no_documents_selected(corpus):
    code = md.main(["--manifest", str(corpus / "manifest.json"), "--domain", "criminal", "--dry-run"])
    assert code == 2


def test_exit_2_when_manifest_missing(tmp_path):
    code = md.main(["--manifest", str(tmp_path / "nope.json"), "--dry-run"])
    assert code == 2


def test_exit_1_when_document_failed(corpus, monkeypatch, capsys):
    from app.rag.ingestion import IngestedDocumentResult

    failed = IngestedDocumentResult(
        document_id="x", chunk_count=0, points_upserted=0, errors=["Qdrant upsert failed"], latency_ms=5
    )

    class FakeIndexer:
        def __init__(self):
            self.ensured = 0
            self.removed = []

        def ensure_collection(self):
            self.ensured += 1

        def remove_document(self, doc_id):
            self.removed.append(doc_id)
            return 0

    class FakePipeline:
        def __init__(self, **kwargs):
            self.indexer = FakeIndexer()

        def ingest_file(self, path, document=None):
            return failed

    calls = {"n": 0}

    def fake_factory(full_enrichment=None, collection=None, cleaner=None):
        calls["n"] += 1
        return FakePipeline()

    monkeypatch.setattr(md, "make_ingestion_pipeline", fake_factory)
    out_dir = corpus / "out"
    code = md.main(["--manifest", str(corpus / "manifest.json"), "--out-dir", str(out_dir)])
    assert code == 1
    assert calls["n"] == 1  # one shared pipeline for the env domain
    summary = json.loads((out_dir / "ingest_multidomain_summary.json").read_text(encoding="utf-8"))
    assert summary["total_failed"] == 2
    assert summary["ok"] is False


def test_cleaner_injection_wired(corpus, monkeypatch):
    """Default passes a DevanagariStrippingCleaner; --no-strip passes None."""
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured["cleaner"] = kwargs.get("cleaner")
            self.indexer = type("I", (), {"ensure_collection": lambda self: None})()

        def ingest_file(self, path, document=None):
            from app.rag.ingestion import IngestedDocumentResult

            return IngestedDocumentResult(chunk_count=1, points_upserted=1)

    monkeypatch.setattr(md, "make_ingestion_pipeline", lambda **kw: FakePipeline(**kw))
    md.main(["--manifest", str(corpus / "manifest.json"), "--out-dir", str(corpus / "out_c")])
    assert isinstance(captured["cleaner"], md.DevanagariStrippingCleaner)

    captured.clear()
    md.main(
        ["--manifest", str(corpus / "manifest.json"), "--no-strip", "--out-dir", str(corpus / "out_c2")]
    )
    assert captured["cleaner"] is None


def test_reindex_removes_prior_points(corpus, monkeypatch):
    from app.rag.ingestion import IngestedDocumentResult

    ok = IngestedDocumentResult(
        document_id="", chunk_count=2, points_upserted=2, errors=[], latency_ms=5
    )

    removed: list[str] = []

    class FakeIndexer:
        def ensure_collection(self):
            pass

        def remove_document(self, doc_id):
            removed.append(doc_id)
            return 0

    class FakePipeline:
        def __init__(self, **kwargs):
            self.indexer = FakeIndexer()

        def ingest_file(self, path, document=None):
            return ok

    monkeypatch.setattr(md, "make_ingestion_pipeline", lambda **kw: FakePipeline())
    code = md.main(
        ["--manifest", str(corpus / "manifest.json"), "--reindex", "--out-dir", str(corpus / "out2")]
    )
    assert code == 0
    assert sorted(removed) == ["clean_doc", "hindi_doc"]


def test_missing_file_counts_as_failed(corpus, monkeypatch):
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    manifest["documents"][0]["file"] = "ghost.txt"
    (corpus / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    code = md.main(["--manifest", str(corpus / "manifest.json"), "--dry-run", "--out-dir", str(corpus / "out3")])
    assert code == 1  # missing file marks the run failed
