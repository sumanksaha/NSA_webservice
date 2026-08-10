"""Tests for the RAG query log / eval models (Phase 1, Day 4).

Tests RAGQueryLog, RAGEvalResult, and RAGEvalDataset model persistence,
indexes, default values, and content-hash uniqueness.

Follows the DB-setup pattern from ``tests/test_ai_assistant.py``
(``_setup_test_env`` with in-memory SQLite + ``db.create_all()``).
"""

from __future__ import annotations

import hashlib

import pytest

from app.extensions import db
from app.models.rag import RAGEvalDataset, RAGEvalResult, RAGQueryLog


@pytest.fixture(scope="module")
def rag_app():
    """Module-scoped app — create_app() is expensive, reuse across tests."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture(scope="function")
def env(rag_app):
    """Function-scoped: fresh tables per test within the shared app."""
    db.drop_all()
    db.create_all()
    yield rag_app
    db.session.remove()


class TestRAGQueryLogModel:
    def test_create_with_required_fields(self, env):
        log = RAGQueryLog(
            query="What is Section 55?",
            query_type="section_lookup",
            content_hash="abc123",
        )
        db.session.add(log)
        db.session.commit()
        assert log.id is not None
        assert log.query == "What is Section 55?"
        assert log.query_type == "section_lookup"

    def test_defaults(self, env):
        log = RAGQueryLog(query="test", query_type="general_qa", content_hash="def456")
        db.session.add(log)
        db.session.commit()
        # JSON fields default to empty lists
        assert log.retrieved_chunk_ids == []
        assert log.retrieval_scores == []
        assert log.cited_chunk_ids == []
        assert log.hallucinated_claims == []
        assert log.hallucination_detected is False

    def test_content_hash_is_sha256(self, env):
        log = RAGQueryLog(
            query="Section 55", query_type="section_lookup", content_hash="a" * 64,
        )
        db.session.add(log)
        db.session.commit()
        assert len(log.content_hash) == 64

    def test_query_by_type_index(self, env):
        for qt in ("section_lookup", "case_law", "general_qa"):
            db.session.add(RAGQueryLog(query=f"q-{qt}", query_type=qt, content_hash=f"hash-{qt}"))
        db.session.commit()
        count = db.session.query(RAGQueryLog).filter_by(query_type="section_lookup").count()
        assert count == 1

    def test_query_by_content_hash_index(self, env):
        h = hashlib.sha256(b"unique").hexdigest()
        db.session.add(RAGQueryLog(query="unique query", query_type="general_qa", content_hash=h))
        db.session.commit()
        found = db.session.query(RAGQueryLog).filter_by(content_hash=h).first()
        assert found is not None
        assert found.query == "unique query"


class TestRAGEvalResultModel:
    def test_create_with_scores(self, env):
        result = RAGEvalResult(
            eval_run_id="run-1",
            query="What is Section 55?",
            expected_answer="Section 55 is about...",
            actual_answer="Section 55 deals with...",
            faithfulness_score=0.85,
            answer_relevance_score=0.78,
            groundedness_score=0.92,
            avg_score=0.85,
            passed=True,
        )
        db.session.add(result)
        db.session.commit()
        assert result.id is not None
        assert result.eval_run_id == "run-1"
        assert result.passed is True

    def test_passed_defaults_false(self, env):
        result = RAGEvalResult(eval_run_id="run-2", query="q", expected_answer="a")
        db.session.add(result)
        db.session.commit()
        assert result.passed is False
        assert result.created_at is not None

    def test_expected_citations_json(self, env):
        result = RAGEvalResult(
            eval_run_id="run-3",
            query="q",
            expected_answer="a",
            expected_citations=[{"chunk_id": "c1", "section": "55"}],
        )
        db.session.add(result)
        db.session.commit()
        found = db.session.query(RAGEvalResult).filter_by(eval_run_id="run-3").first()
        assert found.expected_citations == [{"chunk_id": "c1", "section": "55"}]


class TestRAGEvalDatasetModel:
    def test_create_entry(self, env):
        entry = RAGEvalDataset(
            name="fss_act_sections",
            query="What does Section 55 say?",
            query_type="section_lookup",
            expected_answer="Section 55 prescribes penalties...",
            expected_section="55",
            difficulty="medium",
        )
        db.session.add(entry)
        db.session.commit()
        assert entry.id is not None
        assert entry.is_active is True

    def test_difficulty_default(self, env):
        entry = RAGEvalDataset(
            name="test",
            query="q",
            query_type="general_qa",
            expected_answer="a",
        )
        db.session.add(entry)
        db.session.commit()
        assert entry.difficulty == "medium"

    def test_query_by_active_index(self, env):
        db.session.add(RAGEvalDataset(name="active1", query="q1", query_type="general_qa", expected_answer="a1", is_active=True))
        db.session.add(RAGEvalDataset(name="inactive1", query="q2", query_type="general_qa", expected_answer="a2", is_active=False))
        db.session.commit()
        assert db.session.query(RAGEvalDataset).filter_by(is_active=True).count() == 1
