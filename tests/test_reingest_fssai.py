"""Tests for ``scripts/reingest_fssai_from_db.py`` — P1-4 FSSAI re-ingest (2026-08-11).

Covers the identity-preserving DB -> Qdrant payload rebuild WITHOUT any
network: real SQLite on a temp DB, pure ``build_payload``/``_is_fss_document``,
and CLI guard semantics with a fake indexer/Qdrant client. No Qdrant, no
sentence-transformers, no Flask app required (``_app_context`` is monkeypatched
to a ``FakeAppContext`` with push/pop in the write-path tests).

Key behaviours under test:
- load_corpus reads documents + chunks from a strict read-only SQLite file
- build_payload preserves chunk identity and authoritative row fields
- FSS-scope guard refuses non-FSS documents (exit 2)
- backup guard refuses --delete-collection without the STEP-1 export (exit 2)
- dry-run performs NO Qdrant writes (exit 0)
- --only restricts to a single document
- per-document upsert loop, exit 0/1 semantics with a fake indexer
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import scripts.reingest_fssai_from_db as rdb

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def make_db(tmp_path) -> str:
    """Minimal copy of the legal_document / legal_chunk schema (read-only test)."""
    db = tmp_path / "test_corpus.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE legal_document (
            id TEXT PRIMARY KEY, source_uri TEXT, title TEXT, document_type TEXT,
            authority TEXT, jurisdiction TEXT, effective_date TEXT, enactment_date TEXT,
            amended_date TEXT, is_current INTEGER, qdrant_collection TEXT,
            chunk_count INTEGER, created_at TEXT
        );
        CREATE TABLE legal_chunk (
            id TEXT PRIMARY KEY, document_id TEXT, document_type TEXT,
            section_number TEXT, chunk_index INTEGER, text TEXT, char_count INTEGER,
            word_count INTEGER, hierarchy_level INTEGER, parent_id TEXT,
            citations TEXT, "references" TEXT, entities TEXT, metadata_json TEXT,
            content_hash TEXT, qdrant_point_id TEXT, created_at TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO legal_document VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "doc_fss_act",
            "FSSAI_rules documents/Food_Safety_and_Standards_Act_2006.pdf",
            "Food Safety and Standards Act, 2006",
            "act",
            "Parliament of India",
            "India",
            None,
            "2006-08-24",
            None,
            1,
            "fssai_legal_768",
            2,
            "2026-08-10T10:00:00Z",
        ),
    )
    con.executemany(
        "INSERT INTO legal_chunk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "chunk_0001", "doc_fss_act", "act", "3", 0,
                "Section 3. The Central Government shall make rules.",
                45, 8, 3, None, '[]', '["Section 55"]', '["rule"]',
                json.dumps({"chunk_id": "chunk_0001", "chunk_text": "old", "document_id": "doc_fss_act"}),
                "hash_0001", "chunk_0001", "2026-08-10T10:00:01Z",
            ),
            (
                "chunk_0002", "doc_fss_act", "act", "55", 1,
                "Section 55. Penalty for carrying out business without licence.",
                60, 11, 3, None, '[]', '[]', '[]',
                json.dumps({"chunk_id": "chunk_0002", "chunk_text": "old", "document_id": "doc_fss_act"}),
                "hash_0002", "chunk_0002", "2026-08-10T10:00:02Z",
            ),
        ],
    )
    con.commit()
    con.close()
    return str(db)


class FakeAppContext:
    """Stands in for a Flask app context (``main`` calls push/pop)."""

    def __init__(self):
        self.pushed = 0
        self.popped = 0

    def push(self):
        self.pushed += 1

    def pop(self):
        self.popped += 1


def sample_doc() -> dict:
    return {
        "id": "doc_fss_act",
        "source_uri": "FSSAI_rules documents/Food_Safety_and_Standards_Act_2006.pdf",
        "title": "Food Safety and Standards Act, 2006",
        "document_type": "act",
        "authority": "Parliament of India",
        "jurisdiction": "India",
        "effective_date": None,
        "enactment_date": "2006-08-24",
        "amended_date": None,
        "is_current": 1,
        "qdrant_collection": "fssai_legal_768",
        "chunk_count": 2,
        "created_at": "2026-08-10T10:00:00Z",
    }


def sample_chunk() -> dict:
    return {
        "id": "chunk_0001",
        "document_id": "doc_fss_act",
        "document_type": "act",
        "section_number": "3",
        "chunk_index": 0,
        "text": "Section 3. The Central Government shall make rules.",
        "char_count": 45,
        "word_count": 8,
        "hierarchy_level": 3,
        "parent_id": None,
        "citations": "[]",
        "references": '["Section 55"]',
        "entities": "[]",
        "metadata_json": json.dumps(
            {
                "chunk_id": "chunk_0001",
                "chunk_text": "old",
                "document_id": "doc_fss_act",
                "citations": '["FSS Act s.3"]',
                "references": '["Section 55"]',
            }
        ),
        "content_hash": "hash_0001",
        "qdrant_point_id": "chunk_0001",
        "created_at": "2026-08-10T10:00:01Z",
    }


# --------------------------------------------------------------------------- #
# load_corpus
# --------------------------------------------------------------------------- #


class TestLoadCorpus:
    def test_reads_docs_and_chunks(self, tmp_path):
        docs, chunks = rdb.load_corpus(make_db(tmp_path))
        assert [d["id"] for d in docs] == ["doc_fss_act"]
        assert len(chunks["doc_fss_act"]) == 2
        assert chunks["doc_fss_act"][1]["id"] == "chunk_0002"
        assert chunks["doc_fss_act"][1]["references"] == "[]"  # quoted column read

    def test_missing_db_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            rdb.load_corpus(tmp_path / "nope.db")


# --------------------------------------------------------------------------- #
# build_payload
# --------------------------------------------------------------------------- #


class TestBuildPayload:
    def test_identity_preserved(self):
        pl = rdb.build_payload(sample_chunk(), sample_doc())
        assert pl["chunk_id"] == "chunk_0001"
        assert pl["document_id"] == "doc_fss_act"
        assert pl["act_name"] == rdb.FSS_ACT_NAME
        assert pl["content_hash"] == "hash_0001"
        assert pl["created_at"] == "2026-08-10T10:00:01Z"
        assert pl["section_number"] == "3"
        assert pl["chunk_index"] == 0
        assert pl["chunk_char_count"] == 45
        assert pl["word_count"] == 8

    def test_text_authoritative_over_cached(self):
        """chunk_text must come from the row text, not the cached metadata_json."""
        pl = rdb.build_payload(sample_chunk(), sample_doc())
        assert pl["chunk_text"] == "Section 3. The Central Government shall make rules."
        assert pl["chunk_text"] != "old"

    def test_metadata_json_merged(self):
        """Non-authoritative §5.1 fields flow through from the cached metadata_json."""
        pl = rdb.build_payload(sample_chunk(), sample_doc())
        assert pl["citations"] == '["FSS Act s.3"]'
        assert pl["references"] == '["Section 55"]'
        assert pl["document_title"] == "Food Safety and Standards Act, 2006"
        assert pl["document_uri"].endswith("Food_Safety_and_Standards_Act_2006.pdf")

    def test_broken_metadata_json_falls_back(self):
        chunk = sample_chunk()
        chunk["metadata_json"] = "{not json"
        pl = rdb.build_payload(chunk, sample_doc())
        assert pl["chunk_id"] == "chunk_0001"  # identity still authoritative


# --------------------------------------------------------------------------- #
# FSS-scope guard
# --------------------------------------------------------------------------- #


class TestFssScopeGuard:
    def _corpus(self, with_foreign: bool):
        docs = [sample_doc()]
        if with_foreign:
            docs.append(
                {
                    "id": "doc_foreign",
                    "source_uri": "other domain/some_act.pdf",
                    "title": "Some Other Act",
                    "document_type": "act",
                }
            )
        chunks = {"doc_fss_act": [sample_chunk()]}
        return docs, chunks

    def test_foreign_document_refused_even_in_dry_run(self, monkeypatch, capsys):
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: self._corpus(True))
        code = rdb.main(["--dry-run"])
        assert code == 2
        out = capsys.readouterr().err
        assert "non-FSS" in out
        assert "doc_fore" in out  # ids are truncated to 8 chars in the message

    def test_all_fss_documents_allowed(self, monkeypatch):
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: self._corpus(False))
        code = rdb.main(["--dry-run"])
        assert code == 0

    def test_marker_detection(self):
        assert rdb._is_fss_document(sample_doc())
        assert not rdb._is_fss_document({"id": "x", "source_uri": "other domain/a.pdf", "title": "Y"})


# --------------------------------------------------------------------------- #
# Backup guard
# --------------------------------------------------------------------------- #


class TestBackupGuard:
    def test_delete_requires_backup(self, monkeypatch, tmp_path, capsys):
        docs, chunks = [sample_doc()], {"doc_fss_act": [sample_chunk()]}
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: (docs, chunks))
        monkeypatch.setattr(rdb, "BACKUP_PATH", tmp_path / "missing_backup.json")
        code = rdb.main(["--delete-collection"])
        assert code == 2
        assert "STEP-1 backup" in capsys.readouterr().err

    def test_delete_proceeds_with_backup(self, monkeypatch, tmp_path):
        """With the backup present, the run reaches the indexer (fake)."""
        docs, chunks = [sample_doc()], {"doc_fss_act": [sample_chunk(), dict(sample_chunk(), id="chunk_0002", content_hash="hash_0002")]}
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: (docs, chunks))
        backup = tmp_path / "backup.json"
        backup.write_text("[]")
        monkeypatch.setattr(rdb, "BACKUP_PATH", backup)
        ctx = FakeAppContext()
        monkeypatch.setattr(rdb, "_app_context", lambda: ctx)

        deleted: list[str] = []

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def collection_exists(self, name):
                return True

            def delete_collection(self, name):
                deleted.append(name)

        class FakeIndexer:
            def __init__(self, **k):
                self.kwargs = k

            def ensure_collection(self):
                return True

            def sync_payloads(self, payloads):
                from app.rag.qdrant_indexer import ChunkIngestionResult

                return ChunkIngestionResult(
                    document_id="doc_fss_act",
                    chunk_count=len(payloads),
                    points_upserted=len(payloads),
                    vector_size=768,
                    embedding_model="test",
                )

        import qdrant_client

        import app.rag.qdrant_indexer as qdi

        monkeypatch.setattr(qdrant_client, "QdrantClient", FakeClient)
        monkeypatch.setattr(qdi, "QdrantIndexer", FakeIndexer)

        code = rdb.main(["--delete-collection"])
        assert code == 0
        assert deleted == ["fssai_legal_768"]
        assert ctx.pushed == 1 and ctx.popped == 1  # app context entered + exited


# --------------------------------------------------------------------------- #
# CLI semantics
# --------------------------------------------------------------------------- #


class TestCli:
    def _corpus(self):
        return [sample_doc()], {"doc_fss_act": [sample_chunk(), dict(sample_chunk(), id="chunk_0002")]}

    def test_dry_run_no_writes(self, monkeypatch, capsys):
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: self._corpus())
        code = rdb.main(["--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert "total payloads: 2" in out
        assert "no Qdrant writes" in out

    def test_only_restricts_documents(self, monkeypatch, capsys):
        docs, chunks = self._corpus()
        docs.append(
            {
                "id": "doc_other",
                "source_uri": "FSSAI_rules documents/Other.pdf",
                "title": "",
                "document_type": "regulation",
            }
        )
        chunks["doc_other"] = [dict(sample_chunk(), id="chunk_9001", document_id="doc_other")]
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: (docs, chunks))
        code = rdb.main(["--only", "doc_other", "--dry-run"])
        out = capsys.readouterr().out
        assert code == 0
        assert "total payloads: 1" in out
        # Only the restricted document's line is printed (its uri + chunk count).
        assert "Other.pdf" in out
        assert "chunks=1" in out
        assert "chunks=2" not in out

    def test_only_no_match_exit_2(self, monkeypatch):
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: self._corpus())
        code = rdb.main(["--only", "ghost", "--dry-run"])
        assert code == 2

    def test_failed_document_exit_1(self, monkeypatch, tmp_path):
        docs, chunks = self._corpus()
        monkeypatch.setattr(rdb, "load_corpus", lambda *a, **k: (docs, chunks))
        monkeypatch.setattr(rdb, "_app_context", lambda: FakeAppContext())
        backup = tmp_path / "b.json"
        backup.write_text("[]")
        monkeypatch.setattr(rdb, "BACKUP_PATH", backup)

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def collection_exists(self, name):
                return False  # nothing to delete

        class FakeIndexer:
            def __init__(self, **k):
                pass

            def ensure_collection(self):
                return True

            def sync_payloads(self, payloads):
                from app.rag.qdrant_indexer import ChunkIngestionResult

                return ChunkIngestionResult(
                    chunk_count=len(payloads), points_upserted=0, errors=["upsert failed"]
                )

        import qdrant_client

        import app.rag.qdrant_indexer as qdi

        monkeypatch.setattr(qdrant_client, "QdrantClient", FakeClient)
        monkeypatch.setattr(qdi, "QdrantIndexer", FakeIndexer)

        code = rdb.main([])
        assert code == 1
