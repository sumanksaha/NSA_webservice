"""Phase 1 (de-FSSAI) tests — multi-domain pipeline generalization.

Covers the 2026-08-10 changes that let the FSSAI-only RAG stack ingest and
enrich a multi-domain corpus (environment / commercial / animal / West
Bengal state) while keeping every existing FSSAI path backward compatible:

- ``app/rag/legal_sections.py``  — per-act section registry
- ``query_classifier``           — FSS_ACT_SECTIONS re-exported from registry
- ``crossref_adapter``           — act-aware ``known`` flag
- ``chunker``                    — ``act_name`` payload field
- ``enrichment.deterministic``   — per-document act resolution + legal terms
- ``generation.prompt_template`` — domain-parameterized system prompts
- ``verification.claim_extractor`` — generic statute-name extraction
- collection parameterization   — ``QdrantIndexer(collection_name=...)``,
  ``IngestionPipeline(collection=...)``, ``make_ingestion_pipeline(collection=...)``,
  ``app/rag/collections.py``
"""

from __future__ import annotations

from app.rag.collections import collection_for_domain
from app.rag.enrichment.deterministic import build_deterministic_record, legal_act_of, legal_location_of
from app.rag.legal_sections import (
    FSS_ACT_SECTIONS,
    is_known_section_for_act,
    sections_for_act,
)

AIR_ACT = "Air (Prevention and Control of Pollution) Act, 1981"
COMPANIES_ACT = "Companies Act, 2013"


# --------------------------------------------------------------------------- #
# legal_sections — per-act section registry
# --------------------------------------------------------------------------- #


class TestLegalSections:
    def test_fss_full_set(self):
        assert sections_for_act("Food Safety and Standards Act, 2006") == FSS_ACT_SECTIONS
        assert "1" in FSS_ACT_SECTIONS and "104" in FSS_ACT_SECTIONS
        assert "105" not in FSS_ACT_SECTIONS

    def test_air_act_range(self):
        secs = sections_for_act(AIR_ACT)
        assert secs is not None
        assert "1" in secs and "54" in secs
        assert "55" not in secs  # Air Act ends at 54

    def test_companies_act_range(self):
        secs = sections_for_act(COMPANIES_ACT)
        assert secs is not None
        assert "470" in secs
        assert "471" not in secs

    def test_bns_range(self):
        # Bharatiya Nyaya Sanhita, 2023 — the IPC successor (sections 1–358).
        secs = sections_for_act("Bharatiya Nyaya Sanhita, 2023")
        assert secs is not None
        assert "1" in secs and "358" in secs
        assert "359" not in secs
        assert is_known_section_for_act("302", "Bharatiya Nyaya Sanhita, 2023") is True
        assert is_known_section_for_act("359", "Bharatiya Nyaya Sanhita, 2023") is False

    def test_leading_article_normalized(self):
        assert sections_for_act(f"The {AIR_ACT}") == sections_for_act(AIR_ACT)

    def test_unknown_act_is_none(self):
        assert sections_for_act("Some Hypothetical Act, 2099") is None
        assert sections_for_act(None) is None
        assert sections_for_act("") is None

    def test_short_names_do_not_false_positive(self):
        # Containment fallback is length-guarded: generic fragments must not
        # resolve to an act's section set.
        assert sections_for_act("Act") is None
        assert sections_for_act("2013") is None
        assert sections_for_act("the act") is None

    def test_is_known_section_for_act(self):
        assert is_known_section_for_act("54", AIR_ACT) is True
        assert is_known_section_for_act("55", AIR_ACT) is False  # Air Act ends at 54
        # Sub-clauses resolve against the base number (26 IS an Air Act section).
        assert is_known_section_for_act("26(2)(ii)", AIR_ACT) is True
        assert is_known_section_for_act("55(2)", AIR_ACT) is False
        assert is_known_section_for_act("26(2)(ii)", "Food Safety and Standards Act, 2006") is True
        # Unknown act -> None (unknown), never a false negative
        assert is_known_section_for_act("55", "Some Hypothetical Act, 2099") is None
        assert is_known_section_for_act("", AIR_ACT) is False


class TestQueryClassifierReexport:
    def test_fss_sections_reexported(self):
        from app.rag.retrieval.query_classifier import FSS_ACT_SECTIONS as Q_FSS

        assert Q_FSS == FSS_ACT_SECTIONS
        assert "104" in Q_FSS and "105" not in Q_FSS

    def test_classification_unchanged(self):
        from app.rag.retrieval.query_classifier import QueryClassifier, QueryType

        clf = QueryClassifier()
        assert clf.classify("What does Section 55 say?") == QueryType.SECTION_LOOKUP
        assert clf.classify("What is food safety?") == QueryType.GENERAL_QA


# --------------------------------------------------------------------------- #
# crossref_adapter — act-aware known-ness
# --------------------------------------------------------------------------- #


class TestCrossRefAdapterActAware:
    def test_is_known_section_default_fss(self):
        from app.rag.crossref_adapter import CrossRefAdapter

        adapter = CrossRefAdapter()
        assert adapter.is_known_section("26") is True
        assert adapter.is_known_section("26(2)(ii)") is True
        assert adapter.is_known_section("199") is False
        assert adapter.is_known_section("") is False

    def test_is_known_section_with_act(self):
        from app.rag.crossref_adapter import CrossRefAdapter

        adapter = CrossRefAdapter()
        assert adapter.is_known_section("54", act_name=AIR_ACT) is True
        assert adapter.is_known_section("55", act_name=AIR_ACT) is False
        assert adapter.is_known_section("55", act_name="Unknown Act, 2099") is None

    def test_extract_known_flag_uses_act_name(self):
        from app.cross_reference import CrossReference, ReferenceKind
        from app.rag.crossref_adapter import CrossRefAdapter

        class _FakeEngine:
            def extract_references(self, text):
                return [CrossReference(kind=ReferenceKind.SECTION, target="55", raw="Section 55", position=0, context="", confidence=0.9)]

        adapter = CrossRefAdapter(engine=_FakeEngine())
        ref = adapter.extract("text", act_name=AIR_ACT)[0]
        assert ref.known is False  # 55 is not an Air Act section
        ref_fss = adapter.extract("text")[0]
        assert ref_fss.known is True  # FSS default

    def test_enrich_chunk_uses_chunk_act_name(self):
        from app.cross_reference import CrossReference, ReferenceKind
        from app.rag.crossref_adapter import CrossRefAdapter

        class _FakeEngine:
            def extract_references(self, text):
                return [CrossReference(kind=ReferenceKind.SECTION, target="54", raw="Section 54", position=0, context="", confidence=0.9)]

        class _Chunk:
            def __init__(self):
                self.chunk_text = "See Section 54"
                self.references = None
                self.act_name = AIR_ACT

        chunk = _Chunk()
        CrossRefAdapter(engine=_FakeEngine()).enrich_chunk(chunk)
        assert chunk.references == ["Section 54"]


# --------------------------------------------------------------------------- #
# chunker — act_name payload field
# --------------------------------------------------------------------------- #


class TestChunkerActName:
    def test_act_name_flows_to_payload(self):
        from app.rag.chunker import Chunker
        from app.rag.qdrant_indexer import QdrantIndexer

        chunker = Chunker()
        chunks = chunker.chunk_text(
            "1. Short title. These rules may be called the Rules. 2. (1) An application shall be made.",
            {"document_id": "air_act_1981", "type": "act", "title": AIR_ACT, "act_name": AIR_ACT},
        )
        assert chunks
        payload = chunks[0].to_payload()
        assert payload["act_name"] == AIR_ACT
        # _chunk_from_payload round-trips the new field
        rebuilt = QdrantIndexer._chunk_from_payload(payload)
        assert rebuilt.act_name == AIR_ACT


# --------------------------------------------------------------------------- #
# deterministic enrichment — per-document act resolution
# --------------------------------------------------------------------------- #


def _point(cid, doc, *, text="Body text.", doc_type="regulation", title="Some Regulations, 2020", act_name=None):
    payload = {
        "chunk_id": cid, "document_id": doc, "document_uri": "file:///corpus/x.pdf",
        "document_title": title, "document_type": doc_type, "chunk_text": text,
        "chunk_index": 0, "chunk_char_count": len(text), "word_count": len(text.split()),
        "section_number": None, "section_title": None, "citations": [], "references": [],
        "entities": [], "hierarchy_level": 0, "content_hash": "deadbeef", "act_name": act_name or "",
    }
    return {"id": cid, "payload": payload}


class TestDeterministicActResolution:
    def test_act_document_uses_own_title(self):
        pl = _point("a", "air", doc_type="act", title=f"The {AIR_ACT}")["payload"]
        assert legal_act_of(pl) == AIR_ACT

    def test_explicit_act_name_wins(self):
        pl = _point("a", "reg-1", doc_type="regulation", title="Some Regulation", act_name=COMPANIES_ACT)["payload"]
        assert legal_act_of(pl) == COMPANIES_ACT

    def test_regulation_without_act_name_defaults_to_fss(self):
        # Backward compatible: the FSSAI corpus carries no act_name.
        pl = _point("a", "reg-1", doc_type="regulation", title="Food Safety and Standards (Licensing) Regulations, 2011")["payload"]
        assert legal_act_of(pl) == "Food Safety and Standards Act, 2006"

    def test_unknown_instrument_yields_none(self):
        # A subordinate instrument from another domain without act_name -> no guess.
        pl = _point("a", "x", doc_type="notification", title="Some Notification")["payload"]
        assert legal_act_of(pl) is None

    def test_legal_location_uses_resolved_act(self):
        pl = _point("a", "air", doc_type="act", title=f"The {AIR_ACT}", act_name=AIR_ACT)["payload"]
        loc = legal_location_of(pl, {"section": "21", "title": "", "inherited": False})
        assert loc["act"]["value"] == AIR_ACT
        assert loc["section"]["value"] == "21"

    def test_record_build_uses_act_name(self):
        pl = _point("a", "reg-1", doc_type="regulation", title="PWM Rules", act_name="Environment (Protection) Act, 1986")["payload"]
        rec = build_deterministic_record({"id": "a", "payload": pl}, {"section": None, "title": "", "inherited": False}, [])
        assert rec["legal_location"]["act"]["value"] == "Environment (Protection) Act, 1986"


# --------------------------------------------------------------------------- #
# prompt_template — domain-parameterized system prompts
# --------------------------------------------------------------------------- #


class TestPromptTemplateDomains:
    def test_default_is_fssai(self):
        from app.rag.generation.prompt_template import GROUND_QA_SYSTEM_PROMPT, PromptTemplate

        sys_p, _ = PromptTemplate().render_default("q", "c")
        assert sys_p == GROUND_QA_SYSTEM_PROMPT
        assert "Food Safety and Standards" in sys_p

    def test_env_domain_prompt(self):
        from app.rag.generation.prompt_template import PromptTemplate

        sys_p, _ = PromptTemplate().render_default("q", "c", domain="env")
        assert "environmental law" in sys_p
        assert "Food Safety" not in sys_p

    def test_commercial_domain_via_extra_vars(self):
        from app.rag.generation.prompt_template import PromptTemplate

        sys_p, _ = PromptTemplate().render("grounded_qa", query="q", context="c", extra_vars={"domain": "commercial"})
        assert "corporate law" in sys_p

    def test_criminal_domain_prompt(self):
        from app.rag.generation.prompt_template import PromptTemplate

        sys_p, _ = PromptTemplate().render_default("q", "c", domain="criminal")
        assert "criminal law" in sys_p
        assert "Bharatiya Nyaya Sanhita" in sys_p

    def test_unknown_domain_falls_back_to_default(self):
        from app.rag.generation.prompt_template import GROUND_QA_SYSTEM_PROMPT, PromptTemplate

        sys_p, _ = PromptTemplate().render_default("q", "c", domain="space-law")
        assert sys_p == GROUND_QA_SYSTEM_PROMPT

    def test_user_prompt_unchanged_by_domain(self):
        from app.rag.generation.prompt_template import PromptTemplate

        _, usr_a = PromptTemplate().render_default("q", "c")
        _, usr_b = PromptTemplate().render_default("q", "c", domain="env")
        assert usr_a == usr_b


# --------------------------------------------------------------------------- #
# claim_extractor — generic statute names
# --------------------------------------------------------------------------- #


class TestClaimExtractorStatutes:
    def test_extracts_companies_act(self):
        from app.rag.verification.claim_extractor import ClaimExtractor

        claims = ClaimExtractor().extract("The Companies Act, 2013 requires annual filings.")
        assert claims
        authorities = claims[0].entities.get("authority", [])
        assert any("Companies Act" in a for a in authorities)

    def test_extracts_water_act(self):
        from app.rag.verification.claim_extractor import ClaimExtractor

        claims = ClaimExtractor().extract("The Water (Prevention and Control of Pollution) Act, 1974 governs consent.")
        authorities = claims[0].entities.get("authority", [])
        assert any("Water" in a and "Act" in a for a in authorities)

    def test_lowercase_the_act_not_matched(self):
        from app.rag.verification.claim_extractor import ClaimExtractor

        claims = ClaimExtractor().extract("No court shall take cognizance of the Act without sanction.")
        authorities = claims[0].entities.get("authority", [])
        assert not any(a.strip().lower() == "the act" for a in authorities)

    def test_fss_alternatives_still_extracted(self):
        from app.rag.verification.claim_extractor import ClaimExtractor

        claims = ClaimExtractor().extract("The FSS Act and FSSAI regulations govern this.")
        assert "authority" in claims[0].entities


# --------------------------------------------------------------------------- #
# collection parameterization
# --------------------------------------------------------------------------- #


class TestCollections:
    def test_default_map(self):
        assert collection_for_domain("env") == "env_legal_768"
        assert collection_for_domain("commercial") == "commercial_legal_768"
        assert collection_for_domain("animal") == "animal_legal_768"
        assert collection_for_domain("wb_state") == "wb_state_legal_768"
        assert collection_for_domain("criminal") == "criminal_legal_768"
        assert collection_for_domain("fssai") == "fssai_legal_768"

    def test_aliases_and_fallback(self):
        assert collection_for_domain("food") == "fssai_legal_768"
        assert collection_for_domain("environment") == "env_legal_768"
        assert collection_for_domain("WB_STATE") == "wb_state_legal_768"
        assert collection_for_domain("penal") == "criminal_legal_768"
        assert collection_for_domain("unknown-domain") == "fssai_legal_768"
        assert collection_for_domain(None) == "fssai_legal_768"

    def test_config_override(self):
        cfg = {"RAG_QDRANT_COLLECTION_ENV": "my_env_custom_768"}
        assert collection_for_domain("env", cfg) == "my_env_custom_768"
        assert collection_for_domain("env", {}) == "env_legal_768"
        cfg_crim = {"RAG_QDRANT_COLLECTION_CRIMINAL": "bnc_custom_768"}
        assert collection_for_domain("criminal", cfg_crim) == "bnc_custom_768"


class TestCollectionParameterization:
    def test_indexer_builds_store_against_collection(self):
        from app.rag.qdrant_indexer import QdrantIndexer

        indexer = QdrantIndexer(collection_name="commercial_legal_768")
        assert indexer._store.collection_name == "commercial_legal_768"

    def test_pipeline_threads_collection_to_indexer(self):
        from app.rag.ingestion import IngestionPipeline

        pipeline = IngestionPipeline(collection="env_legal_768")
        assert pipeline._collection == "env_legal_768"
        assert pipeline.indexer._store.collection_name == "env_legal_768"

    def test_make_ingestion_pipeline_accepts_collection(self):
        from app.rag.ingestion import make_ingestion_pipeline

        pipeline = make_ingestion_pipeline(full_enrichment=False, collection="animal_legal_768")
        assert pipeline._collection == "animal_legal_768"
        # Default stays None -> config-driven collection (backward compat).
        assert make_ingestion_pipeline(full_enrichment=False)._collection is None
