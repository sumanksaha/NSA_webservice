"""Tests for Phase 2 RAG generation pipeline (app/rag/generation/).

Covers: ContextBuilder, PromptTemplate, GroundedLLMClient,
CitationTracker, ResponseSanitizer, GroundedGenerationService,
GenerationLogger, and run_generation_pipeline.

All tests use STUB LLM mode (no API key) so no network is required.
DB-dependent tests (GenerationLogger) spin up in-memory SQLite.
"""

from __future__ import annotations

import hashlib

from app.rag.generation import (
    ContextBuilder,
    GroundedGenerationService,
    GroundedLLMClient,
    GroundedLLMResponse,
    PromptTemplate,
    CitationTracker,
    ResponseSanitizer,
    GenerationLogger,
)
from app.rag.generation.context_builder import BuiltContext
from app.rag.retrieval.result import RetrievedChunk, Citation
from app.rag.tasks import run_generation_pipeline


def _make_chunks(n=3):
    return [
        RetrievedChunk(
            chunk_id=f"c{i}",
            score=0.9 - i * 0.1,
            text=f"Section {i + 1} of the FSS Act, 2006.",
            section_number=str(i + 1),
            document_title="FSS Act 2006",
            document_type="act",
            authority="FSSAI",
        )
        for i in range(n)
    ]


class TestContextBuilder:
    def test_builds_context_with_citations(self):
        built = ContextBuilder(max_chunks=10).build("query", _make_chunks(3))
        assert len(built.citations) == 3
        assert "[Source 1]" in built.context
        assert "[Source 3]" in built.context
        assert not built.truncated

    def test_citations_have_metadata(self):
        built = ContextBuilder().build("q", _make_chunks(2))
        assert built.citations[0]["index"] == 1
        assert built.citations[0]["chunk_id"] == "c0"
        assert built.citations[0]["section_number"] == "1"

    def test_empty_chunks_returns_empty(self):
        built = ContextBuilder().build("query", [])
        assert built.context == ""
        assert built.chunk_count == 0

    def test_truncation_when_exceeding_max_chars(self):
        built = ContextBuilder(max_context_chars=100, max_chunks=50).build("q", _make_chunks(20))
        assert built.truncated

    def test_max_chunks_limit(self):
        built = ContextBuilder(max_chunks=3).build("q", _make_chunks(10))
        assert built.chunk_count == 3
        assert built.truncated

    def test_sorts_by_score_descending(self):
        chunks = [
            RetrievedChunk(chunk_id="low", score=0.1, text="low", section_number="1"),
            RetrievedChunk(chunk_id="high", score=0.9, text="high", section_number="2"),
        ]
        built = ContextBuilder().build("q", chunks)
        assert built.citations[0]["chunk_id"] == "high"

    def test_token_estimate_positive(self):
        built = ContextBuilder().build("q", _make_chunks(1))
        assert built.total_tokens_estimate > 0


class TestPromptTemplate:
    def test_render_default(self):
        tpl = PromptTemplate()
        sys_p, usr_p = tpl.render_default("my query", "my context")
        assert "my query" in usr_p
        assert "my context" in usr_p
        assert len(sys_p) > 10

    def test_available_actions(self):
        assert "grounded_qa" in PromptTemplate().available_actions

    def test_unknown_action_raises(self):
        try:
            PromptTemplate().render("unknown", query="q", context="c")
            assert False, "Should raise"
        except ValueError:
            pass

    def test_extra_vars_merged(self):
        tpl = PromptTemplate()
        sys_p, usr_p = tpl.render("grounded_qa", query="q", context="c", extra_vars={"foo": "bar"})
        assert "q" in usr_p


class TestGroundedLLMClient:
    def test_stub_success(self):
        resp = GroundedLLMClient().call("sys", "usr")
        assert resp.success
        assert "stub" in resp.model

    def test_stub_custom_response(self):
        resp = GroundedLLMClient(stub_response="Custom [1]").call("s", "u")
        assert resp.text == "Custom [1]"

    def test_success_false_on_error(self):
        assert not GroundedLLMResponse(error="fail").success

    def test_success_false_on_empty(self):
        assert not GroundedLLMResponse(text="", model="m").success

    def test_stub_usage(self):
        resp = GroundedLLMClient().call("s", "u")
        assert resp.usage["prompt_tokens"] > 0

    def test_model_default_is_laguna(self):
        """The default model is poolside/laguna-s-2.1:free."""
        assert GroundedLLMClient().model == "poolside/laguna-s-2.1:free"

    def test_model_explicit_arg_overrides(self):
        """An explicit model arg overrides the default."""
        assert (
            GroundedLLMClient(model="meta-llama/llama-3.3-70b-instruct:free").model
            == "meta-llama/llama-3.3-70b-instruct:free"
        )

    def test_model_env_var_overrides_default(self, monkeypatch):
        """RAG_LLM_MODEL env var overrides the default model."""
        monkeypatch.setenv("RAG_LLM_MODEL", "openai/gpt-4o-mini")
        assert GroundedLLMClient().model == "openai/gpt-4o-mini"


class TestCitationTracker:
    def test_bracket_citations(self):
        chunks = _make_chunks(3)
        cits = CitationTracker().extract("See [1] and [3]", chunks)
        assert len(cits) == 2
        assert cits[0].chunk_id == "c0"
        assert cits[1].chunk_id == "c2"

    def test_section_references(self):
        chunks = _make_chunks(3)
        cits = CitationTracker().extract("Section 2 and Section 3", chunks)
        ids = {c.chunk_id for c in cits}
        assert "c1" in ids and "c2" in ids

    def test_deduplication(self):
        cits = CitationTracker().extract("[1] and [1] again", _make_chunks(2))
        assert len(cits) == 1

    def test_empty_response(self):
        assert CitationTracker().extract("", _make_chunks(2)) == []

    def test_custom_citation_map(self):
        chunks = _make_chunks(3)
        cm = {1: chunks[2], 2: chunks[0]}
        cits = CitationTracker().extract("[1] and [2]", chunks, citation_map=cm)
        assert cits[0].chunk_id == "c2"
        assert cits[1].chunk_id == "c0"

    def test_out_of_range_ignored(self):
        cits = CitationTracker().extract("[1] and [99]", _make_chunks(2))
        assert len(cits) == 1


class TestResponseSanitizer:
    def test_all_valid(self):
        chunks = _make_chunks(3)
        cits = CitationTracker().extract("See [1] and [2]", chunks)
        san = ResponseSanitizer().sanitize("text", cits, chunks)
        assert len(san.valid_citations) == 2
        assert san.groundedness_score == 1.0
        assert not san.hallucination_detected

    def test_invalid_citation_flagged(self):
        chunks = _make_chunks(2)
        valid = Citation(
            chunk_id="c0",
            section_number="1",
            document_title="FSS Act",
            document_type="act",
            authority="FSSAI",
            url=None,
            snippet="t",
            confidence=0.85,
        )
        invalid = Citation(
            chunk_id="fake",
            section_number=None,
            document_title="Fake",
            document_type="act",
            authority="FSSAI",
            url=None,
            snippet="t",
            confidence=0.5,
        )
        san = ResponseSanitizer().sanitize("r", [valid, invalid], chunks)
        assert len(san.valid_citations) == 1
        assert len(san.invalid_citations) == 1
        assert san.hallucination_detected

    def test_section_not_in_chunks_flagged(self):
        chunks = _make_chunks(1)
        cits = CitationTracker().extract("Section 1 and Section 99", chunks)
        san = ResponseSanitizer().sanitize("Section 1 and Section 99", cits, chunks)
        assert any("Section 99" in c for c in san.hallucinated_claims)

    def test_no_citations_low_groundedness(self):
        chunks = _make_chunks(2)
        san = ResponseSanitizer().sanitize("no cites", [], chunks)
        assert san.groundedness_score == 0.0
        assert san.hallucination_detected

    def test_confidence_in_range(self):
        chunks = _make_chunks(2)
        cits = CitationTracker().extract("[1] and [2]", chunks)
        san = ResponseSanitizer().sanitize("t", cits, chunks)
        assert 0.0 <= san.confidence <= 1.0


class TestGroundedGenerationService:
    def test_full_pipeline_stub(self):
        result = GroundedGenerationService().generate("Question", _make_chunks(3), "section_lookup")
        assert result.answer
        assert result.llm_model == "stub-poolside/laguna-s-2.1:free"
        assert result.token_usage["prompt"] == 100
        assert result.debug["chunk_count"] == 3

    def test_empty_chunks_short_circuits(self):
        result = GroundedGenerationService().generate("q", [], "general_qa")
        assert result.answer == ""
        assert result.llm_model == ""
        assert result.debug.get("empty_context") is True

    def test_custom_llm_client(self):
        client = GroundedLLMClient(stub_response="Custom [1]")
        result = GroundedGenerationService(llm_client=client).generate("q", _make_chunks(1))
        assert "Custom" in result.answer

    def test_citation_map_uses_context_order(self):
        chunks = [
            RetrievedChunk(chunk_id="low", score=0.1, text="low", section_number="1"),
            RetrievedChunk(chunk_id="high", score=0.9, text="high", section_number="2"),
        ]
        result = GroundedGenerationService().generate("query", chunks)
        cited = [c.chunk_id for c in result.citations]
        assert "high" in cited


class TestRunGenerationPipeline:
    def test_with_pre_provided_chunks(self):
        chunks = [
            {
                "chunk_id": "c1",
                "score": 0.9,
                "text": "Section 55 text",
                "section_number": "55",
                "document_title": "FSS Act",
                "document_type": "act",
                "authority": "FSSAI",
            }
        ]
        result = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")
        assert result["query"] == "Section 55?"
        assert result["query_type"] == "section_lookup"
        assert result["answer"]
        assert "llm_model" in result
        assert "token_usage" in result
        assert "groundedness_score" in result

    def test_with_none_chunks_delegates_to_retrieval(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.tasks.run_retrieval_pipeline",
            lambda **kw: {"chunks": [], "query_type": "general_qa", "retrieval_latency_ms": 0},
        )
        result = run_generation_pipeline(query="test", chunks=None)
        assert result["query"] == "test"
        assert result["answer"] == ""
        assert result["groundedness_score"] == 0.0
        assert result["query_type"] == "general_qa"

    def test_kg_contract_fusion_off_by_default(self, monkeypatch):
        """RAG_KG_FUSION defaults off: no KG contract provisions injected, and
        no KG call is made."""
        chunks = [
            {
                "chunk_id": "c1", "score": 0.9, "text": "Section 55 text",
                "section_number": "55", "document_title": "FSS Act",
                "document_type": "act", "authority": "FSSAI",
            }
        ]
        called = []

        def _fake_provisions_for_query(*a, **k):
            called.append(a)
            return [{"provision_id": "P1", "provision_number": "55", "title": "T"}]

        monkeypatch.setattr("kg.queries.provisions_for_query", _fake_provisions_for_query)
        result = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")
        assert result["kg_contract"] is None
        assert called == []

    def test_kg_contract_fusion_injects_and_fuses(self, monkeypatch):
        """RAG_KG_FUSION on: the retrieval contract's provisions become KG
        chunks and are RRF-fused into the context; the response reports the
        injection."""
        chunks = [
            {
                "chunk_id": "c1", "score": 0.9, "text": "Section 55 text",
                "section_number": "55", "document_title": "FSS Act",
                "document_type": "act", "authority": "FSSAI",
            }
        ]

        def _fake_provisions_for_query(query, kg_queries, limit=10):
            assert query == "Section 55?"
            return [
                {
                    # Novel provision (Air Act s3) — NOT covered by the vector
                    # chunks, so it survives the KG-redundancy dedup.
                    "provision_id": "P1", "provision_number": "3", "title": "Prov 3",
                    "instrument_title": "Air (Prevention and Control of Pollution) Act, 1981",
                    "legal_domain": "ENVIRONMENT_POLLUTION",
                    "status": "current", "text": "Provision 3 body",
                }
            ]

        monkeypatch.setattr("kg.queries.provisions_for_query", _fake_provisions_for_query)
        monkeypatch.setenv("RAG_KG_FUSION", "true")
        result = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")
        assert result["kg_contract"] is not None
        assert result["kg_contract"]["provisions"] == 1
        assert result["kg_contract"]["injected"] == 1
        assert result["kg_contract"]["fused"] is True
        # The KG provision chunk must be part of the evidence handed to the LLM.
        ids = [c.get("chunk_id") for c in result["retrieved_chunks"]]
        assert any(str(i).startswith("KG:") for i in ids)

    def test_kg_contract_fusion_best_effort_on_error(self, monkeypatch):
        """A failing KG contract degrades to no fusion (never raises)."""
        chunks = [
            {
                "chunk_id": "c1", "score": 0.9, "text": "Section 55 text",
                "section_number": "55", "document_title": "FSS Act",
                "document_type": "act", "authority": "FSSAI",
            }
        ]

        def _boom(*a, **k):
            raise RuntimeError("neo4j down")

        monkeypatch.setattr("kg.queries.provisions_for_query", _boom)
        monkeypatch.setenv("RAG_KG_FUSION", "true")
        result = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")
        assert result["kg_contract"]["error"] == "neo4j down"
        assert result["kg_contract"]["fused"] is False
        assert result["answer"]  # generation still worked


class TestRetrievedChunkFromDict:
    def test_round_trip(self):
        chunk = _make_chunks(1)[0]
        restored = RetrievedChunk.from_dict(chunk.to_dict())
        assert restored.chunk_id == chunk.chunk_id
        assert restored.score == chunk.score
        assert restored.text == chunk.text
        assert restored.section_number == chunk.section_number

    def test_from_dict_missing_optional_keys(self):
        d = {"chunk_id": "x", "score": 0.8, "text": "hello"}
        chunk = RetrievedChunk.from_dict(d)
        assert chunk.chunk_id == "x"
        assert chunk.section_number is None


class TestGenerationLogger:
    def _setup(self):
        from app import create_app
        from app.extensions import db
        from app.models import FSO, User
        from app.models.rag import RAGQueryLog

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        db.session.add(User(username="gl", password_hash="pbkdf2:sha256$test$dummy"))
        db.session.add(FSO(fso_name="Test Officer"))
        db.session.commit()
        log = RAGQueryLog(
            query="What is Section 55?", query_type="section_lookup", content_hash=hashlib.sha256(b"test").hexdigest()
        )
        db.session.add(log)
        db.session.commit()
        return ctx, log

    def _teardown(self, ctx):
        from app.extensions import db

        db.session.remove()
        db.drop_all()
        ctx.pop()

    def test_updates_fields(self):
        ctx, log = self._setup()
        try:
            gl = GenerationLogger(actor="test-agent")
            result = gl.log_generation(
                log.id,
                query="What is Section 55?",
                response_text="Section 55 deals with licensing [1]",
                cited_chunk_ids=["c1", "c2"],
                groundedness_score=0.85,
                hallucination_detected=False,
                total_latency_ms=200,
                prompt_tokens=100,
                completion_tokens=30,
                llm_model="stub",
            )
            assert result is not None
            assert result.response_text == "Section 55 deals with licensing [1]"
            assert result.cited_chunk_ids == ["c1", "c2"]
            assert result.groundedness_score == 0.85
        finally:
            self._teardown(ctx)

    def test_missing_log_returns_none(self):
        ctx, log = self._setup()
        try:
            gl = GenerationLogger()
            assert gl.log_generation("nonexistent-id", query="q", response_text="r") is None
        finally:
            self._teardown(ctx)

    def test_hallucination_fields(self):
        ctx, log = self._setup()
        try:
            GenerationLogger(actor="test").log_generation(
                log.id,
                query="test",
                hallucination_detected=True,
                hallucinated_claims=["Section 999 does not exist"],
                groundedness_score=0.1,
            )
            from app.extensions import db
            from app.models.rag import RAGQueryLog as _RQL

            updated = db.session.get(_RQL, log.id)
            assert updated.hallucination_detected is True
            assert "Section 999 does not exist" in updated.hallucinated_claims
        finally:
            self._teardown(ctx)


class TestGenerateRoute:
    def _setup(self):
        from app import create_app
        from app.extensions import db
        from app.models import FSO, User

        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        user = User(username="routeuser", password_hash="pbkdf2:sha256$test$dummy")
        db.session.add(user)
        db.session.add(FSO(fso_name="Test Officer"))
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
        return client, ctx

    def _teardown(self, ctx):
        from app.extensions import db

        db.session.remove()
        db.drop_all()
        ctx.pop()

    def test_generate_with_chunks(self):
        client, ctx = self._setup()
        try:
            resp = client.post(
                "/api/rag/generate",
                json={
                    "query": "What does Section 1 say?",
                    "chunks": [
                        {
                            "chunk_id": "c1",
                            "score": 0.9,
                            "text": "Section 1 text.",
                            "section_number": "1",
                            "document_title": "FSS Act",
                        }
                    ],
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["query"] == "What does Section 1 say?"
            assert data["answer"]
        finally:
            self._teardown(ctx)

    def test_missing_query_returns_400(self):
        client, ctx = self._setup()
        try:
            resp = client.post("/api/rag/generate", json={})
            assert resp.status_code == 400
        finally:
            self._teardown(ctx)
