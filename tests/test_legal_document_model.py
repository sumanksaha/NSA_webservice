"""Tests for the Agent A corpus models + hook registration (scope §5.2/5.3).

``LegalDocument`` (file_hash UNIQUE) and ``LegalChunk`` (content_hash,
unique ``(document_id, chunk_index)``) back the Day 4 ingestion pipeline and
the Day 5 SHA-256 dedup.  Tests run against the isolated temp SQLite DB used
by the whole suite (``tests/conftest.py``) via ``db.create_all()``.
"""

from __future__ import annotations

import uuid

import pytest

from app.extensions import db
from app.models import LegalChunk, LegalDocument


@pytest.fixture
def test_client():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            yield client


class TestLegalDocumentModel:
    def test_table_created_and_columns(self, test_client):
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(db.engine)
        assert "legal_document" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("legal_document")}
        for col in ("id", "source_uri", "document_type", "file_hash", "status",
                    "qdrant_collection", "chunk_count", "created_at", "updated_at"):
            assert col in columns, f"legal_document missing column {col!r}"

    def test_insert_and_unique_file_hash(self, test_client):
        doc = LegalDocument(
            source_uri="s3://corpus/fss-act-2006.pdf",
            document_type="act",
            file_hash="a" * 64,
            status="indexed",
            chunk_count=42,
        )
        db.session.add(doc)
        db.session.commit()
        assert doc.id
        assert doc.qdrant_collection == "fssai_legal_768"
        # Duplicate file_hash violates the UNIQUE constraint (SHA-256 dedup).
        dup = LegalDocument(
            source_uri="s3://corpus/other.pdf",
            document_type="act",
            file_hash="a" * 64,
        )
        db.session.add(dup)
        with pytest.raises(Exception):  # IntegrityError on SQLite/Postgres
            db.session.commit()
        db.session.rollback()

    def test_indexes_present(self, test_client):
        from sqlalchemy import inspect as sa_inspect

        indexes = {ix["name"] for ix in sa_inspect(db.engine).get_indexes("legal_document")}
        assert "idx_legal_document_status" in indexes
        assert "idx_legal_document_type" in indexes


class TestLegalChunkModel:
    def test_insert_requires_content_hash(self, test_client):
        chunk = LegalChunk(
            id=str(uuid.uuid4()),
            document_id="doc-1",
            document_type="act",
            chunk_index=0,
            text="Section 55 text",
            char_count=15,
            word_count=3,
            content_hash="b" * 64,
        )
        db.session.add(chunk)
        db.session.commit()
        assert chunk.hierarchy_level == 0
        assert chunk.qdrant_point_id is None

    def test_unique_document_chunk_index(self, test_client):
        def _row():
            return LegalChunk(
                id=str(uuid.uuid4()),
                document_id="doc-uq",
                document_type="act",
                chunk_index=5,
                text="text",
                char_count=4,
                word_count=1,
                content_hash="c" * 64,
            )

        db.session.add(_row())
        db.session.commit()
        db.session.add(_row())  # same (document_id, chunk_index)
        with pytest.raises(Exception):  # uq_chunk_doc_index
            db.session.commit()
        db.session.rollback()

    def test_indexes_present(self, test_client):
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(db.engine)
        indexes = {ix["name"] for ix in inspector.get_indexes("legal_chunk")}
        assert "idx_legal_chunk_doc_section" in indexes
        assert "idx_legal_chunk_parent" in indexes
        assert "idx_legal_chunk_content_hash" in indexes


class TestRegisterLegalChunkHooks:
    def test_registers_both_models_then_unregisters(self):
        from app.rag import qdrant_indexer as qi

        qi.unregister_model(LegalChunk)
        qi.unregister_model(LegalDocument)
        try:
            qi.register_legal_chunk_hooks()
            assert LegalChunk in qi._REGISTERED_MODELS
            assert qi._REGISTERED_MODELS[LegalChunk] == "chunk"
            assert LegalDocument in qi._REGISTERED_MODELS
            assert qi._REGISTERED_MODELS[LegalDocument] == "document"
        finally:
            qi.unregister_model(LegalChunk)
            qi.unregister_model(LegalDocument)
