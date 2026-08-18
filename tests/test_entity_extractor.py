"""Tests for the Agent A §3.4 legal entity extractor (app/rag/entity_extractor.py).

Covers the three-tier strategy: rule-based (always available), spaCy NER
fallback (injected fake), and LLM fallback (injected fake, only when spaCy is
unavailable), plus the dual §5.1/§5.2 payload shape and the ingestion-pipeline
wiring (opt-in adapter + full-enrichment factory default).
"""

from __future__ import annotations

import json

from app.rag.entity_extractor import (
    LegalEntity,
    LegalEntityExtractor,
    _dedupe,
    _looks_like_entity,
)

# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _FakeNER:
    """spaCy-style NER backend returning grouped entities."""

    def __init__(self, grouped=None):
        self._grouped = grouped or {}
        self.calls = 0

    def extract_entities(self, text):
        self.calls += 1
        return self._grouped


class _FakeLLM:
    """GroundedLLMClient-style double returning a canned JSON payload."""

    def __init__(self, payload: str | None = None):
        self._payload = payload
        self.calls = []

    def call(self, system_prompt, user_prompt, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt, **kwargs})
        text = self._payload or (
            '[{"name": "Justice S. Ravindra Bhat", "type": "person", "confidence": 0.9},'
            '{"name": "Nestle India Ltd.", "type": "organization", "confidence": 0.85},'
            '{"name": "Criminal Appeal No. 1234 of 2004", "type": "case", "confidence": 0.8},'
            '{"name": "Section 55", "type": "statute", "confidence": 0.75}]'
        )
        return type("Resp", (), {"text": text})()


class _FakeLLMFailing:
    def call(self, system_prompt, user_prompt, **kwargs):
        raise RuntimeError("llm down")


class _FakeChunk:
    def __init__(self, text):
        self.chunk_text = text
        self.entities = []


_SAMPLE = (
    "The Food Safety and Standards Authority of India issued a notice. "
    "Hon'ble Justice S. Ravindra Bhat heard the matter in W.P. (C) No. 123/2006. "
    "Nestle India Pvt. Ltd. challenged the order under Section 55 of the "
    "Food Safety and Standards Act, 2006."
)


# --------------------------------------------------------------------------- #
# Tier 1 — rule-based extraction
# --------------------------------------------------------------------------- #


class TestRuleBased:
    def test_extracts_person_judge(self):
        result = LegalEntityExtractor().extract("Hon'ble Justice S. Ravindra Bhat heard the appeal.")
        types = {e.entity_type: e.name for e in result.entities}
        assert types.get("person", "").startswith("Hon'ble Justice")

    def test_extracts_organization(self):
        result = LegalEntityExtractor().extract(
            "Nestle India Pvt. Ltd. and the Food Safety and Standards Authority of India agreed."
        )
        names = {e.name for e in result.entities if e.entity_type == "organization"}
        assert any("Nestle India Pvt. Ltd." in n for n in names)
        assert any("Food Safety and Standards Authority of India" in n for n in names)

    def test_extracts_case_number(self):
        result = LegalEntityExtractor().extract("The matter was W.P. (C) No. 123/2006 before the Court.")
        cases = {e.name for e in result.entities if e.entity_type == "case"}
        assert any("W.P. (C) No. 123/2006" in c for c in cases)

    def test_extracts_statute_provision(self):
        result = LegalEntityExtractor().extract("Penalties arise under Section 55 of the Act.")
        statutes = [e for e in result.entities if e.entity_type == "statute"]
        assert any(e.name.startswith("Section 55") for e in statutes)

    def test_full_sample_payload_names(self):
        result = LegalEntityExtractor().extract(_SAMPLE)
        names = result.payload_names()
        assert names  # non-empty
        assert all(isinstance(n, str) and n for n in names)
        # All four entity types present on the realistic sample.
        types = {e.entity_type for e in result.entities}
        assert types == {"person", "organization", "case", "statute"}

    def test_empty_text_short_circuit(self):
        result = LegalEntityExtractor().extract("")
        assert result.entities == []
        assert not result.ok
        result = LegalEntityExtractor().extract("   \n  ")
        assert result.entities == []

    def test_entities_capped(self):
        text = "Justice A. Kumar. " * 200  # 200 candidate matches
        result = LegalEntityExtractor().extract(text)
        assert len(result.entities) <= 100

    def test_dedupe_keeps_first(self):
        a = LegalEntity(name="Section 55", entity_type="statute", confidence=0.9)
        b = LegalEntity(name="section 55", entity_type="statute", confidence=0.7)
        c = LegalEntity(name="Section 56", entity_type="statute", confidence=0.8)
        deduped = _dedupe([a, b, c])
        assert len(deduped) == 2
        assert deduped[0].confidence == 0.9  # first wins

    def test_looks_like_entity_rejects_lowercase(self):
        assert _looks_like_entity("Section 55")
        assert _looks_like_entity("Nestle")
        assert not _looks_like_entity("the act")
        assert not _looks_like_entity("x")


# --------------------------------------------------------------------------- #
# Tier 2 — spaCy NER fallback
# --------------------------------------------------------------------------- #


class TestSpacyFallback:
    def test_ner_entities_mapped_and_merged(self):
        fake = _FakeNER({
            "PERSON": [("Justice A. Kumar", 0.8)],
            "ORG": [("FSSAI", 0.7)],
            "LAW": [("Food Safety and Standards Act", 0.6)],
            "DATE": [("2006", 0.9)],  # unmapped -> dropped
        })
        extractor = LegalEntityExtractor(ner=fake)
        result = extractor.extract("Justice A. Kumar and FSSAI under the Food Safety and Standards Act.")
        assert fake.calls == 1
        types = {e.entity_type for e in result.entities}
        assert types == {"person", "organization", "statute"}
        by_type = {e.entity_type: e for e in result.entities}
        assert by_type["person"].name == "Justice A. Kumar"
        assert by_type["organization"].name == "FSSAI"
        assert by_type["statute"].method == "ner"

    def test_ner_failure_is_best_effort(self):
        class _Boom:
            def extract_entities(self, text):
                raise RuntimeError("boom")

        extractor = LegalEntityExtractor(ner=_Boom())
        # Rule-based still runs; NER failure yields no crash.
        result = extractor.extract("Section 55 of the Act.")
        assert any(e.entity_type == "statute" for e in result.entities)

    def test_ner_rejects_lowercase_fragments(self):
        fake = _FakeNER({"ORG": [("the act", 0.8), ("FSSAI", 0.7)]})
        result = LegalEntityExtractor(ner=fake).extract("FSSAI issued an order.")
        names = [e.name for e in result.entities if e.entity_type == "organization"]
        assert "FSSAI" in names
        assert "the act" not in names


# --------------------------------------------------------------------------- #
# Tier 3 — LLM fallback (only when spaCy unavailable)
# --------------------------------------------------------------------------- #


class TestEnvGate:
    def test_llm_disabled_by_default(self, monkeypatch):
        """Without RAG_ENTITY_LLM=true, no LLM client is built (offline-safe)."""
        monkeypatch.delenv("RAG_ENTITY_LLM", raising=False)
        extractor = LegalEntityExtractor()
        assert extractor._get_llm() is None

    def test_llm_env_gate_requires_true(self, monkeypatch):
        monkeypatch.setenv("RAG_ENTITY_LLM", "true")
        extractor = LegalEntityExtractor()
        # A real GroundedLLMClient is built when the env gate is on.
        llm = extractor._get_llm()
        assert llm is not None
        assert llm.__class__.__name__ == "GroundedLLMClient"

    def test_llm_env_false_after_true_is_cached_disabled(self, monkeypatch):
        monkeypatch.setenv("RAG_ENTITY_LLM", "false")
        extractor = LegalEntityExtractor()
        assert extractor._get_llm() is None
        monkeypatch.setenv("RAG_ENTITY_LLM", "true")
        # The False sentinel is cached — still None (no repeated env reads).
        assert extractor._get_llm() is None


class TestLLMFallback:
    def test_llm_used_when_ner_unavailable(self):
        extractor = LegalEntityExtractor(llm=_FakeLLM())
        result = extractor.extract(_SAMPLE)
        types = {e.entity_type for e in result.entities}
        assert "person" in types
        assert "organization" in types
        assert all(e.method in ("regex", "llm") for e in result.entities)
        assert "llm" in result.methods_used

    def test_llm_not_used_when_ner_available(self):
        """§3.4: LLM is the fallback for an ABSENT spaCy, not an augmenter."""
        fake_ner = _FakeNER({"PERSON": [("Justice A. Kumar", 0.8)]})
        llm = _FakeLLM()
        extractor = LegalEntityExtractor(ner=fake_ner, llm=llm)
        result = extractor.extract(_SAMPLE)
        assert llm.calls == []  # never invoked
        assert "llm" not in result.methods_used
        assert "ner" in result.methods_used

    def test_llm_json_parse_flexible(self):
        llm = _FakeLLM(payload='```json\n[{"name": "FSSAI", "type": "organization", "confidence": 0.9}]\n```')
        result = LegalEntityExtractor(llm=llm).extract("FSSAI issued an order.")
        orgs = [e for e in result.entities if e.entity_type == "organization"]
        assert any(e.name == "FSSAI" and e.method == "llm" for e in orgs)

    def test_llm_bad_json_returns_rules_only(self):
        llm = _FakeLLM(payload="sorry, I cannot do that")
        result = LegalEntityExtractor(llm=llm).extract(_SAMPLE)
        assert "llm" in result.methods_used
        # Rule-based results still present, no crash.
        assert any(e.method == "regex" for e in result.entities)

    def test_llm_invalid_entries_skipped(self):
        llm = _FakeLLM(payload='[{"name": "", "type": "organization"}, {"name": 123, "type": "bogus"}, "x"]')
        result = LegalEntityExtractor(llm=llm).extract("some text")
        assert all(e.name for e in result.entities)

    def test_llm_failure_is_best_effort(self):
        extractor = LegalEntityExtractor(llm=_FakeLLMFailing())
        result = extractor.extract(_SAMPLE)
        assert result.ok  # rules-only result
        assert "llm" in result.methods_used


# --------------------------------------------------------------------------- #
# Enrichment + payload/DB shape
# --------------------------------------------------------------------------- #


class TestEnrichment:
    def test_enrich_chunk_sets_entities_from_text(self):
        chunk = _FakeChunk("Hon'ble Justice S. Ravindra Bhat heard the appeal under Section 55.")
        extractor = LegalEntityExtractor()
        returned = extractor.enrich_chunk(chunk)
        assert returned is chunk
        assert isinstance(chunk.entities, list)
        assert all(isinstance(n, str) and n for n in chunk.entities)

    def test_enrich_chunk_skips_empty_text(self):
        chunk = _FakeChunk("")
        LegalEntityExtractor().enrich_chunk(chunk)
        assert chunk.entities == []

    def test_enrich_document_never_clobbers(self):
        extractor = LegalEntityExtractor()
        merged = extractor.enrich_document(
            {"document_id": "doc-1", "entities": [{"name": "Kept", "type": "person", "confidence": 1.0}]},
            _SAMPLE,
        )
        assert merged["entities"] == [{"name": "Kept", "type": "person", "confidence": 1.0}]
        assert "entity_extraction" in merged  # cache key always stamped

    def test_enrich_document_fills_missing_and_is_json_safe(self):
        merged = LegalEntityExtractor().enrich_document({"document_id": "doc-1"}, _SAMPLE)
        assert merged["entities"]
        json.dumps(merged)  # must be JSON-serializable

    def test_structured_shape_matches_s52(self):
        structured = LegalEntityExtractor().structured_entities("Nestle India Pvt. Ltd. — Section 55.")
        assert structured
        for item in structured:
            assert set(item) == {"name", "type", "confidence", "method"}
            assert item["type"] in {"person", "organization", "case", "statute"}

    def test_enrich_document_then_chunk_payload_is_plain_names(self):
        """Structured dicts from enrich_document never leak into the payload."""
        from app.rag.chunker import Chunk

        extractor = LegalEntityExtractor()
        enriched = extractor.enrich_document({"document_id": "doc-1"}, _SAMPLE)
        assert enriched["entities"]  # structured dicts at the doc level
        chunk = Chunk.from_paragraph(
            {"paragraph_id": "p0", "text": "Justice A. Kumar", "hierarchy_depth": 1},
            enriched,
        )
        assert isinstance(chunk.entities, list)
        assert all(isinstance(e, str) for e in chunk.entities)  # no dicts in payload
        json.dumps(chunk.to_payload())  # payload stays JSON-safe


# --------------------------------------------------------------------------- #
# Pipeline wiring
# --------------------------------------------------------------------------- #


class TestPipelineWiring:
    def test_pipeline_wires_entity_extractor_in_full_enrichment(self):
        from app.rag.ingestion import make_ingestion_pipeline

        pipeline = make_ingestion_pipeline(full_enrichment=True)
        from app.rag.entity_extractor import LegalEntityExtractor

        assert isinstance(pipeline._entity_extractor, LegalEntityExtractor)

    def test_pipeline_default_does_not_wire_heavy_entity_extractor(self):
        from app.rag.ingestion import make_ingestion_pipeline

        pipeline = make_ingestion_pipeline()  # cheap default
        assert pipeline._entity_extractor is None

    def test_pipeline_enriches_chunk_entities_end_to_end(self):
        """Full chain stamps chunk.entities on the produced chunks."""
        from app.rag.chunk_quality import ChunkQualityValidator
        from app.rag.citation_adapter import CitationAdapter
        from app.rag.crossref_adapter import CrossRefAdapter
        from app.rag.document_classifier import DocumentClassifier
        from app.rag.entity_extractor import LegalEntityExtractor
        from app.rag.ingestion import IngestionPipeline

        chunker = _RecordingChunker([("c0", "Nestle India Pvt. Ltd. challenged the order under Section 55.")])
        indexer = _RecordingIndexer(chunker)
        pipeline = IngestionPipeline(
            indexer=indexer,
            metadata_adapter=None,
            citation_adapter=CitationAdapter(),
            crossref_adapter=CrossRefAdapter(),
            classifier=DocumentClassifier(),
            entity_extractor=LegalEntityExtractor(),
            quality_validator=ChunkQualityValidator(),
        )
        result = pipeline.ingest_text(
            "Nestle India Pvt. Ltd. challenged the order under Section 55.",
            {"document_id": "doc-1"},
        )
        assert result.ok
        synced = indexer.sync_calls[0]
        assert synced
        assert isinstance(synced[0].entities, list)
        assert any("Section 55" in e for e in synced[0].entities)


# --------------------------------------------------------------------------- #
# Doubles (mirror tests/test_ingestion_pipeline.py)
# --------------------------------------------------------------------------- #


class _RecordingChunker:
    def __init__(self, specs):
        self._specs = specs

    def chunk_text(self, text, document=None):
        from app.rag.chunker import Chunk

        doc = dict(document or {})
        return [
            Chunk(
                chunk_id=cid,
                document_id=str(doc.get("document_id") or "doc-1"),
                chunk_index=i,
                chunk_text=ctext,
                document_type=doc.get("document_type") or doc.get("type") or "",
                authority=doc.get("authority", ""),
            )
            for i, (cid, ctext) in enumerate(self._specs)
        ]


class _RecordingIndexer:
    def __init__(self, chunker):
        self.chunker = chunker
        self.sync_calls = []

    def sync_chunks(self, chunks):
        from app.rag.qdrant_indexer import ChunkIngestionResult

        self.sync_calls.append(list(chunks))
        return ChunkIngestionResult(
            document_id=str(chunks[0].document_id if chunks else "doc-1"),
            chunk_count=len(chunks),
            points_upserted=len(chunks),
        )


# End of test_entity_extractor.py
