"""Tests for the RAG corpus ingestion CLI (scripts/ingest_corpus.py).

Covers the three ingestion modes (corpus_dir / --file / --text), the
``--full-enrichment`` flag threading into ``make_ingestion_pipeline``,
JSON output, and exit-code semantics (0 success / duplicate, 1 failed, 2
usage/error).  The ingestion entry points are monkeypatched so no Qdrant,
sentence-transformers, or real documents are required.
"""

from __future__ import annotations

import json

import scripts.ingest_corpus as cli

_SUCCESS_DOC = {
    "document_id": "doc-1",
    "source_uri": "/corpus/a.txt",
    "file_type": "txt",
    "file_hash": "hash",
    "text_chars": 10,
    "chunk_count": 2,
    "duplicate_chunks": 0,
    "points_upserted": 2,
    "duplicate": False,
    "latency_ms": 5,
    "errors": [],
    "quality_summary": None,
    "ok": True,
}

_SUCCESS_SUMMARY = {
    "corpus_dir": "/corpus",
    "total": 1,
    "indexed": 1,
    "duplicates": 0,
    "failed": 0,
    "results": [_SUCCESS_DOC],
}


class TestArgParsing:
    def test_no_input_returns_usage_error(self, monkeypatch):
        """No corpus_dir / --file / --text -> ValueError -> exit 2."""
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main([]) == 2


class TestIngestModes:
    def test_corpus_dir_mode(self, monkeypatch, capsys):
        seen = {}

        def fake_pipeline(full_enrichment=None):
            seen["full_enrichment"] = full_enrichment
            return "pipeline"

        def fake_ingest_corpus(corpus_dir, document=None, pipeline=None):
            seen["corpus_dir"] = corpus_dir
            seen["pipeline"] = pipeline
            return _SUCCESS_SUMMARY

        monkeypatch.setattr("scripts.ingest_corpus.make_ingestion_pipeline", fake_pipeline)
        monkeypatch.setattr("scripts.ingest_corpus.ingest_corpus_dir", fake_ingest_corpus)
        code = cli.main(["/corpus"])
        assert code == 0
        assert seen["corpus_dir"] == "/corpus"
        assert seen["pipeline"] == "pipeline"
        assert seen["full_enrichment"] is None  # flag not passed
        output = json.loads(capsys.readouterr().out)
        assert output["total"] == 1
        assert output["indexed"] == 1

    def test_file_mode(self, monkeypatch, capsys):
        seen = {}

        def fake_run(source, document=None, pipeline=None):
            seen["source"] = source
            return _SUCCESS_DOC

        monkeypatch.setattr("scripts.ingest_corpus.run_ingest_document", fake_run)
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        code = cli.main(["--file", "/corpus/a.pdf"])
        assert code == 0
        assert seen["source"] == "/corpus/a.pdf"
        assert json.loads(capsys.readouterr().out)["ok"] is True

    def test_text_mode(self, monkeypatch, capsys):
        seen = {}

        def fake_run(source, document=None, pipeline=None):
            seen["source"] = source
            return _SUCCESS_DOC

        monkeypatch.setattr("scripts.ingest_corpus.run_ingest_document", fake_run)
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        code = cli.main(["--text", "The Food Safety and Standards Act, 2006"])
        assert code == 0
        assert seen["source"] == "The Food Safety and Standards Act, 2006"


class TestFullEnrichmentFlag:
    def test_flag_forces_full_enrichment(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("scripts.ingest_corpus.make_ingestion_pipeline",
                            lambda full_enrichment=None: seen.setdefault("full_enrichment", full_enrichment))
        monkeypatch.setattr("scripts.ingest_corpus.ingest_corpus_dir",
                            lambda corpus_dir, document=None, pipeline=None: _SUCCESS_SUMMARY)
        code = cli.main(["/corpus", "--full-enrichment"])
        assert code == 0
        assert seen["full_enrichment"] is True

    def test_no_flag_leaves_default(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("scripts.ingest_corpus.make_ingestion_pipeline",
                            lambda full_enrichment=None: seen.setdefault("full_enrichment", full_enrichment))
        monkeypatch.setattr("scripts.ingest_corpus.ingest_corpus_dir",
                            lambda corpus_dir, document=None, pipeline=None: _SUCCESS_SUMMARY)
        cli.main(["/corpus"])
        assert seen["full_enrichment"] is None  # resolve flag normally


class TestOutputFormat:
    def test_pretty_flag_indents_json(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "scripts.ingest_corpus.run_ingest_document",
            lambda source, document=None, pipeline=None: _SUCCESS_DOC,
        )
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main(["--text", "some text", "--pretty"]) == 0
        out = capsys.readouterr().out
        assert "\n  \"document_id\"" in out  # indented pretty-print
        assert json.loads(out)["ok"] is True


class TestExitCodes:
    def test_exit_1_when_document_failed(self, monkeypatch):
        failed = dict(_SUCCESS_DOC)
        failed["ok"] = False
        failed["errors"] = ["Qdrant upsert failed"]
        monkeypatch.setattr(
            "scripts.ingest_corpus.run_ingest_document",
            lambda source, document=None, pipeline=None: failed,
        )
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main(["--text", "some text"]) == 1

    def test_exit_1_when_corpus_failed(self, monkeypatch):
        summary = dict(_SUCCESS_SUMMARY)
        summary["failed"] = 1
        monkeypatch.setattr(
            "scripts.ingest_corpus.ingest_corpus_dir",
            lambda corpus_dir, document=None, pipeline=None: summary,
        )
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main(["/corpus"]) == 1

    def test_exit_2_on_value_error(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.ingest_corpus.ingest_corpus_dir",
            lambda corpus_dir, document=None, pipeline=None: (_ for _ in ()).throw(ValueError("bad dir")),
        )
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main(["/nonexistent"]) == 2

    def test_exit_2_on_file_not_found(self, monkeypatch):
        monkeypatch.setattr(
            "scripts.ingest_corpus.run_ingest_document",
            lambda source, document=None, pipeline=None: (_ for _ in ()).throw(
                FileNotFoundError("File not found: /nope.pdf")
            ),
        )
        monkeypatch.setattr(
            "scripts.ingest_corpus.make_ingestion_pipeline", lambda full_enrichment=None: "pipeline"
        )
        assert cli.main(["--file", "/nope.pdf"]) == 2
