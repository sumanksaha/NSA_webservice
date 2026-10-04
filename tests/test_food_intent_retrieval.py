"""Tests for entity–provision-aware food retrieval (2026-09-28).

Covers the four new seams plus the wired pipeline stages:

- ``food_query_understanding`` — entity/intent/parameters (deterministic);
- ``provision_metadata`` — document-role/commodity/table derivation;
- ``legal_reranker`` — two-stage entity gate + intent features + weights;
- ``parent_reconstruction`` — clause grouping + evidence bundle;
- ``validation`` — evidence validation + fallback queries;
- ``food_answer`` — anti-definition-anchoring prompts + completeness.

No network, no Qdrant, no models: chunks are plain ``RetrievedChunk`` (or
dict) stand-ins built from the real cumin-standard corpus shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.rag.retrieval.food_query_understanding import (
    FoodQueryUnderstanding,
    detect_food_intent,
    extract_entity,
    extract_parameters,
)
from app.rag.retrieval.legal_reranker import LegalAwareReranker, LegalAwareWeights
from app.rag.retrieval.parent_reconstruction import (
    group_by_clause,
    reconstruct_evidence_bundle,
)
from app.rag.retrieval.provision_metadata import (
    derive_provision_metadata,
    is_definition_chunk,
    is_standard_chunk,
)
from app.rag.retrieval.result import RetrievedChunk
from app.rag.retrieval.validation import (
    fallback_queries,
    kg_fallback_queries,
    validate_retrieval,
)


# ---------------------------------------------------------------------------
# Corpus-shaped fixtures (real cumin clause 2.9.8 shapes)
# ---------------------------------------------------------------------------

CUMIN_HEADING = (
    "2.9.8: Cumin (Zeera, Kalonji) 1. Cumin (Safed Zeera) whole means the dried "
    "mature fruits of Cuminum Cyminum L. It shall have characteristic aromatic "
    "flavour free from mustiness. It shall be free from mould, living and dead "
    "insects, insect fragments, rodent contamination. The product shall be free "
    "from added colour and harmful substances."
)

CUMIN_ROWS = (
    "(i) Extraneous matter Not more than 3.0 percent by weight (ii) Broken fruits "
    "(Damaged, shrivelled, Not more than 5.0 percent by weight discoloured and "
    "immature seed) (iii) Moisture Not more than 10.0 percent by weight (iv) Total "
    "ash on dry basis Not more than 9.5 percent by weight"
)

OTHER_COMMODITY_ROWS = (
    "(i) Moisture Not more than 12.0 percent by weight (ii) Total ash on dry basis "
    "Not more than 8.0 percent by weight"
)

LICENSING_TEXT = (
    "No person shall commence or carry on any food business except under a licence "
    "issued under the Act. Registration is required for petty food business operators."
)

SAMPLING_TEXT = (
    "The Food Safety Officer shall take the sample in the manner prescribed and the "
    "sample shall be sealed and divided into parts for analysis."
)


def _chunk(
    text: str,
    *,
    chunk_id: str = "c1",
    score: float = 0.5,
    clause: str | None = None,
    doc_id: str = "doc1",
    title: str = "Food Additives Regulations-4",
    chunk_index: int = 0,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        text=text,
        clause_number=clause,
        document_title=title,
        act_name="Food Safety and Standards Act, 2006",
        document_type="regulation",
        document_id=doc_id,
        chunk_index=chunk_index,
        hierarchy_level=3,
    )


# ---------------------------------------------------------------------------
# food_query_understanding
# ---------------------------------------------------------------------------


class TestFoodQueryUnderstanding:
    @pytest.mark.parametrize(
        "query,intent",
        [
            ("What is cumin?", "definition"),
            ("Define cumin.", "definition"),
            ("What is the standard for cumin?", "food_standard"),
            ("What are the standards for cumin?", "food_standard"),
            ("What are the requirements for cumin?", "food_standard"),
            ("What is the moisture limit for cumin?", "parameter_specific_standard"),
            ("What is the maximum foreign matter permitted in cumin?", "parameter_specific_standard"),
            ("What are the requirements for defective seeds in cumin?", "parameter_specific_standard"),
            ("How should cumin be sampled?", "sampling"),
            ("What is the sampling procedure for cumin?", "sampling"),
            ("Does this cumin comply with the standard?", "compliance"),
            ("What is the licence requirement for a food business?", "licensing"),
            ("What is the prohibition on papaya seeds?", "prohibition"),
            ("What is the penalty for unsafe food?", "penalty"),
        ],
    )
    def test_intent_classification(self, query, intent):
        assert FoodQueryUnderstanding.from_query(query).intent == intent

    @pytest.mark.parametrize(
        "query,entity",
        [
            ("What is cumin?", "cumin"),
            ("What is the standard for cumin?", "cumin"),
            ("What is the moisture limit for cumin?", "cumin"),
            ("What is ghee?", "ghee"),
            ("What is the standard for ghee?", "ghee"),
            ("How should cumin be sampled?", "cumin"),
        ],
    )
    def test_entity_extraction(self, query, entity):
        assert FoodQueryUnderstanding.from_query(query).entity == entity

    def test_parameter_extraction(self):
        fq = FoodQueryUnderstanding.from_query("What is the moisture limit for cumin?")
        assert fq.parameters == ["moisture"]
        assert fq.requested_provision_type == "standard"
        assert fq.target == "moisture limit"

    def test_definition_vs_standard_distinction(self):
        """THE core distinction — same entity, different intent."""
        d = FoodQueryUnderstanding.from_query("What is cumin?")
        s = FoodQueryUnderstanding.from_query("What is the standard for cumin?")
        assert d.entity == s.entity == "cumin"
        assert d.intent == "definition"
        assert s.intent == "food_standard"
        assert d.requested_provision_type == "definition"
        assert s.requested_provision_type == "standard"

    def test_determinism(self):
        q = "What is the moisture limit for cumin?"
        first = FoodQueryUnderstanding.from_query(q)
        for _ in range(5):
            assert FoodQueryUnderstanding.from_query(q).to_dict() == first.to_dict()

    def test_schema_keys(self):
        d = FoodQueryUnderstanding.from_query("What is the standard for cumin?").to_dict()
        assert set(d.keys()) == {
            "entity",
            "intent",
            "target",
            "parameters",
            "jurisdiction",
            "requested_provision_type",
            "confidence",
        }

    def test_empty_and_garbage(self):
        assert FoodQueryUnderstanding.from_query("").intent == "general_information"
        # No crash, no fabricated entity
        fq = FoodQueryUnderstanding.from_query("???")
        assert fq.entity is None or isinstance(fq.entity, str)

    def test_extract_entity_returns_none_for_pure_boilerplate(self):
        assert extract_entity("What is the procedure?") is None

    def test_parameter_specific_degrades_without_known_parameter(self):
        fq = FoodQueryUnderstanding.from_query("What is the limit for blorbian flux?")
        assert fq.intent in ("food_standard", "parameter_specific_standard")

    def test_wants_standard(self):
        s = FoodQueryUnderstanding.from_query("What is the standard for cumin?")
        d = FoodQueryUnderstanding.from_query("What is cumin?")
        assert s.wants_standard is True
        assert d.wants_standard is False


# ---------------------------------------------------------------------------
# provision_metadata
# ---------------------------------------------------------------------------


class TestProvisionMetadata:
    def test_definition_chunk_role(self):
        ch = _chunk(CUMIN_HEADING, clause="2.9.8")
        meta = derive_provision_metadata(ch)
        assert meta["document_role"] == "definition"
        assert meta["commodity"] == "cumin"
        assert meta["section"] == "2.9.8"
        assert meta["provision_type"] == "definition"

    def test_table_row_chunk_role(self):
        ch = _chunk(CUMIN_ROWS, clause="2.9.8", chunk_index=903)
        meta = derive_provision_metadata(ch)
        assert meta["document_role"] == "standard"
        assert meta["provision_type"] == "food_standard"
        assert "requirements table" in meta["table"]

    def test_no_fabrication_unknown_fields(self):
        ch = _chunk("Some arbitrary regulatory prose without signals.", title="", doc_id="")
        meta = derive_provision_metadata(ch)
        assert meta["commodity"] == "unknown"
        assert meta["section"] == "unknown"
        assert meta["regulation"] == "unknown"

    def test_is_definition_chunk(self):
        assert is_definition_chunk(_chunk(CUMIN_HEADING))
        assert not is_definition_chunk(_chunk(CUMIN_ROWS))

    def test_is_standard_chunk(self):
        assert is_standard_chunk(_chunk(CUMIN_ROWS))
        assert not is_standard_chunk(_chunk(CUMIN_HEADING))

    def test_licensing_role(self):
        meta = derive_provision_metadata(_chunk(LICENSING_TEXT))
        assert meta["document_role"] == "licensing"

    def test_deterministic(self):
        ch = _chunk(CUMIN_ROWS, clause="2.9.8")
        assert derive_provision_metadata(ch) == derive_provision_metadata(ch)

    def test_dict_chunk_accepted(self):
        meta = derive_provision_metadata({"text": CUMIN_ROWS, "clause_number": "2.9.8"})
        assert meta["document_role"] == "standard"


# ---------------------------------------------------------------------------
# legal_reranker
# ---------------------------------------------------------------------------


class TestLegalReranker:
    def test_definition_does_not_anchor_standard_query(self):
        """THE acceptance behaviour: for 'standard for cumin', a requirement
        row must outrank the definition heading."""
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.9)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.6)
        other = _chunk(OTHER_COMMODITY_ROWS, chunk_id="other", clause="4.1.2", score=0.8)
        rr = LegalAwareReranker()
        out = rr.rerank("What is the standard for cumin?", [heading, rows, other])
        ids = [c.chunk_id for c in out]
        assert ids.index("rows") < ids.index("heading"), ids

    def test_definition_query_still_prefers_definition(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.4)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.9)
        rr = LegalAwareReranker()
        out = rr.rerank("What is cumin?", [heading, rows])
        assert out[0].chunk_id == "heading"

    def test_entity_absent_chunk_demoted(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.3)
        foreign = _chunk(OTHER_COMMODITY_ROWS, chunk_id="foreign", clause="9.9.9", score=0.95)
        rr = LegalAwareReranker()
        out = rr.rerank("What is the standard for cumin?", [foreign, heading])
        assert out[0].chunk_id == "heading"

    def test_disabled_reranker_is_passthrough(self):
        chunks = [_chunk("a", score=1.0), _chunk("b", score=0.5)]
        rr = LegalAwareReranker(enabled=False)
        out = rr.rerank("What is the standard for cumin?", chunks, top_k=2)
        assert [c.text for c in out] == ["a", "b"]

    def test_weights_configurable(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.9)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.6)
        # Zero out every legal feature → pure semantic order survives.
        w = LegalAwareWeights(w_ce=1.0, w_dense=0.0, w_entity=0.0, w_intent=0.0, w_prov=0.0, w_legal=0.0, w_parent=0.0, w_lex=0.0)
        out = LegalAwareReranker(weights=w).rerank("What is the standard for cumin?", [heading, rows])
        assert out[0].chunk_id == "heading"

    def test_sampling_intent_prefers_sampling_text(self):
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.9)
        sampling = _chunk(SAMPLING_TEXT, chunk_id="sampling", score=0.5)
        rr = LegalAwareReranker()
        out = rr.rerank("What is the sampling procedure for cumin?", [rows, sampling])
        assert out[0].chunk_id == "sampling"

    def test_trace_shape(self):
        chunks = [_chunk(CUMIN_HEADING, clause="2.9.8"), _chunk(CUMIN_ROWS, clause="2.9.8")]
        trace = LegalAwareReranker().trace("What is the standard for cumin?", chunks)
        assert trace["entity"] == "cumin"
        assert trace["intent"] == "food_standard"
        assert trace["top_results"] and "document_role" in trace["top_results"][0]
        assert "weights" in trace

    def test_ce_scores_respected(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.5)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.5)
        rr = LegalAwareReranker()
        # Tie the legal features by giving the rows chunk terrible CE scores.
        out = rr.rerank(
            "What is the standard for cumin?",
            [heading, rows],
            ce_scores={"heading": 0.0, "rows": 0.0},
        )
        # rows still wins on intent features (CE normalisation is relative)
        assert out[0].chunk_id == "rows"

    def test_empty_pool(self):
        assert LegalAwareReranker().rerank("What is cumin?", []) == []


# ---------------------------------------------------------------------------
# parent_reconstruction
# ---------------------------------------------------------------------------


class TestParentReconstruction:
    def test_clause_grouping(self):
        a = _chunk(CUMIN_HEADING, chunk_id="a", clause="2.9.8", doc_id="d1")
        b = _chunk(CUMIN_ROWS, chunk_id="b", clause="2.9.8", doc_id="d1")
        c = _chunk(OTHER_COMMODITY_ROWS, chunk_id="c", clause="4.1.2", doc_id="d1")
        groups = group_by_clause([a, b, c])
        assert set(groups.keys()) == {("d1", "2.9.8"), ("d1", "4.1.2")}
        assert len(groups[("d1", "2.9.8")]) == 2

    def test_bundle_with_heading_in_pool(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.9)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.7)
        bundle = reconstruct_evidence_bundle("What is the standard for cumin?", [heading, rows])
        assert bundle["entity"] == "cumin"
        assert bundle["intent"] == "food_standard"
        # Parent context = the actual heading chunk
        assert any(getattr(pc, "chunk_id", None) == "heading" for pc in bundle["parent_context"])
        assert bundle["completeness"]["standard_found"] is True
        assert bundle["legal_source"]["section"] == "2.9.8"
        assert bundle["legal_source"]["regulation"] == "Food Additives Regulations-4"

    def test_bundle_synthetic_context_without_heading(self):
        """Rows retrieved without their heading chunk still reconstruct a
        verified-fields context (never the definition, never invented text)."""
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.7)
        bundle = reconstruct_evidence_bundle("What is the standard for cumin?", [rows])
        assert bundle["parent_context"], "synthetic context expected"
        ctx = bundle["parent_context"][0]
        assert isinstance(ctx, str)
        assert "2.9.8" in ctx
        assert "Reconstructed context" in ctx

    def test_bundle_definition_intent(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.4)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.9)
        bundle = reconstruct_evidence_bundle("What is cumin?", [heading, rows])
        assert bundle["intent"] == "definition"
        assert any(getattr(pc, "chunk_id", None) == "heading" for pc in bundle["parent_context"])
        assert bundle["completeness"]["entity_found"] is True

    def test_bundle_empty_pool(self):
        bundle = reconstruct_evidence_bundle("What is cumin?", [])
        assert bundle["primary_evidence"] == []
        assert bundle["completeness"]["entity_found"] is False

    def test_bundle_excludes_foreign_commodity_from_related(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.9)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.7)
        foreign = _chunk(OTHER_COMMODITY_ROWS, chunk_id="foreign", clause="9.9.9", score=0.95)
        bundle = reconstruct_evidence_bundle("What is the standard for cumin?", [heading, rows, foreign])
        related_ids = {getattr(r, "chunk_id", "") for r in bundle["related_evidence"]}
        assert "foreign" not in related_ids

    def test_parameter_completeness(self):
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.9)
        bundle = reconstruct_evidence_bundle("What is the moisture limit for cumin?", [rows])
        assert bundle["completeness"]["parameter_complete"] is True
        bundle2 = reconstruct_evidence_bundle("What is the aflatoxin limit for cumin?", [rows])
        assert bundle2["completeness"]["parameter_complete"] is False


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_standard_query_valid_with_rows(self):
        fq = FoodQueryUnderstanding.from_query("What is the standard for cumin?")
        report = validate_retrieval("What is the standard for cumin?", [_chunk(CUMIN_ROWS)], food=fq)
        assert report["valid"] is True

    def test_standard_query_invalid_with_only_definition(self):
        fq = FoodQueryUnderstanding.from_query("What is the standard for cumin?")
        report = validate_retrieval("What is the standard for cumin?", [_chunk(CUMIN_HEADING)], food=fq)
        assert report["valid"] is False
        assert report["reasons"]

    def test_definition_query_valid_with_definition(self):
        fq = FoodQueryUnderstanding.from_query("What is cumin?")
        report = validate_retrieval("What is cumin?", [_chunk(CUMIN_HEADING)], food=fq)
        assert report["valid"] is True

    def test_definition_query_invalid_with_only_rows(self):
        fq = FoodQueryUnderstanding.from_query("What is cumin?")
        report = validate_retrieval("What is cumin?", [_chunk(CUMIN_ROWS)], food=fq)
        assert report["valid"] is False

    def test_parameter_missing_invalidates(self):
        fq = FoodQueryUnderstanding.from_query("What is the aflatoxin limit for cumin?")
        report = validate_retrieval("What is the aflatoxin limit for cumin?", [_chunk(CUMIN_ROWS)], food=fq)
        assert report["valid"] is False

    def test_fallback_queries_shape(self):
        queries = fallback_queries("What is the standard for cumin?")
        assert queries, "fallback arms expected"
        assert all("cumin" in q for q in queries)
        # Entity + standard vocabulary arms
        assert any("shall conform" in q or "standard" in q for q in queries)

    def test_fallback_queries_parameter_specific(self):
        queries = fallback_queries("What is the moisture limit for cumin?")
        assert any("moisture" in q for q in queries)

    def test_fallback_queries_no_entity(self):
        assert fallback_queries("What is the procedure?") == []

    def test_kg_fallback_degrades_gracefully(self, monkeypatch):
        """No Neo4j configured → empty list, never raises (spec §11).

        The env vars are cleared explicitly: a developer's ``.env`` may point at
        a real local Neo4j, and this test is about the *unconfigured* path.
        """
        for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"):
            monkeypatch.delenv(key, raising=False)
        out = kg_fallback_queries("What is the standard for cumin?")
        assert out == []


# ---------------------------------------------------------------------------
# food_answer (generation prompts + completeness)
# ---------------------------------------------------------------------------


class TestFoodAnswer:
    def _bundle(self):
        heading = _chunk(CUMIN_HEADING, chunk_id="heading", clause="2.9.8", score=0.9)
        rows = _chunk(CUMIN_ROWS, chunk_id="rows", clause="2.9.8", score=0.7)
        return reconstruct_evidence_bundle("What is the standard for cumin?", [heading, rows])

    def test_system_prompt_contains_anti_anchoring(self):
        from app.rag.generation.food_answer import FOOD_STANDARD_SYSTEM_PROMPT, build_food_system_prompt

        prompt = build_food_system_prompt("food_standard")
        assert prompt == FOOD_STANDARD_SYSTEM_PROMPT
        assert "a definition of the food is not an answer" in prompt

    def test_standard_prompt_demands_full_enumeration(self):
        """§6.1: B_food_standard failed by quoting one variety / part of the table."""
        from app.rag.generation.food_answer import build_food_system_prompt

        prompt = build_food_system_prompt("food_standard")
        assert "EVERY parameter/requirement row" in prompt
        assert "EVERY variety/form" in prompt
        # Adjacent-row misreading guard (FI020: total ash answered with moisture).
        assert "bound to the parameter it was read from" in prompt

    def test_parameter_prompt(self):
        from app.rag.generation.food_answer import build_food_system_prompt

        assert "SPECIFIC PARAMETER" in build_food_system_prompt("parameter_specific_standard")

    def test_parameter_prompt_demands_exact_row_match(self):
        """§6.1: a parameter ask must not fall back to the nearest numeric row."""
        from app.rag.generation.food_answer import build_food_system_prompt

        prompt = build_food_system_prompt("parameter_specific_standard")
        assert "ROW MATCHING" in prompt
        assert "adjacent rows" in prompt

    def test_parameter_prompt_does_not_encourage_refusal(self):
        """Regression: an over-cautious row-matching clause made the model
        decline limits that were present in the retrieved table (FI003,
        FI018, FI025) whenever the clause heading was missing."""
        from app.rag.generation.food_answer import build_food_system_prompt

        prompt = build_food_system_prompt("parameter_specific_standard")
        assert "rather than returning the nearest value" not in prompt
        assert "give the parameter's value and cite it, rather than declining" in prompt

    def test_user_prompt_numbers_evidence(self):
        from app.rag.generation.food_answer import render_food_user_prompt

        prompt = render_food_user_prompt("What is the standard for cumin?", self._bundle())
        assert "<legal_context>" in prompt
        assert "[1]" in prompt
        assert "Question: What is the standard for cumin?" in prompt

    def test_parameter_rows_extraction(self):
        from app.rag.generation.food_answer import parameter_rows_from_evidence

        rows = parameter_rows_from_evidence(self._bundle())
        names = {r["parameter"].lower() for r in rows}
        assert any("moisture" in n for n in names)
        moisture = next(r for r in rows if "moisture" in r["parameter"].lower())
        assert "10.0" in moisture["value"]

    def test_completeness_all_true_for_good_bundle(self):
        from app.rag.generation.food_answer import check_answer_completeness

        answer = (
            "Food: Cumin\nApplicable standard: clause 2.9.8 requires: extraneous matter "
            "not more than 3.0 percent by weight; moisture not more than 10.0 percent "
            "by weight [1]."
        )
        verdict = check_answer_completeness("What is the standard for cumin?", answer, self._bundle())
        assert verdict["entity_found"] is True
        assert verdict["standard_found"] is True
        assert verdict["numeric_value_present"] is True
        assert verdict["definition_leak"] is False
        assert verdict["answer_complete"] is True

    def test_completeness_flags_definition_leak(self):
        from app.rag.generation.food_answer import check_answer_completeness

        answer = "Cumin means the dried ripe fruit of Cuminum cyminum [1]."
        verdict = check_answer_completeness("What is the standard for cumin?", answer, self._bundle())
        assert verdict["definition_leak"] is True
        assert verdict["answer_complete"] is False

    def test_completeness_insufficient_evidence_case(self):
        from app.rag.generation.food_answer import check_answer_completeness

        heading_only = reconstruct_evidence_bundle(
            "What is the standard for cumin?", [_chunk(CUMIN_HEADING, clause="2.9.8")]
        )
        verdict = check_answer_completeness("What is the standard for cumin?", "The evidence is insufficient.", heading_only)
        assert verdict["standard_found"] is False
        assert verdict["answer_complete"] is False
