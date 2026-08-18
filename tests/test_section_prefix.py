"""Tests for CV2 P1 (section-prefix train/serve parity) + P4 (domain balance).

Covers:
  - app.rag.retrieval.section_prefix: prefix_passage formatting, flag gating,
    idempotency, section-over-clause precedence, no-identity fallback
  - Reranker / EnsembleReranker pair construction applies the prefix when the
    flag is on (and not when off — zero behavior change for v1)
  - evaluation.pairwise_dataset: authoritative payload-join identity
    propagation, --section-prefix baking, --domain-balanced oversampling

All offline — no Qdrant, no sentence-transformers, no torch.
"""

from __future__ import annotations

import json

from app.rag.retrieval.result import RetrievedChunk
from app.rag.retrieval.section_prefix import prefix_passage

# --------------------------------------------------------------------------- #
# section_prefix
# --------------------------------------------------------------------------- #


class TestPrefixPassage:
    def test_off_by_default(self, monkeypatch):
        """Flag default off → passage unchanged (v1-compatible)."""
        monkeypatch.delenv("RAG_CE_SECTION_PREFIX", raising=False)
        assert prefix_passage("duties of the Authority", "16") == "duties of the Authority"

    def test_section_prefix_when_enabled(self, monkeypatch):
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        assert prefix_passage("duties of the Authority", "16") == "\u00a716 duties of the Authority"

    def test_section_normalised_to_base(self, monkeypatch):
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        assert prefix_passage("text", "16(2)(a)") == "\u00a716 text"

    def test_clause_fallback_for_regulations(self, monkeypatch):
        """Regulation chunks have no section → clause is the identity (G7/G8)."""
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        assert prefix_passage("text", None, "2.4.15") == "\u00a72.4.15 text"

    def test_section_wins_over_clause(self, monkeypatch):
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        assert prefix_passage("text", "16", "2.4.15") == "\u00a716 text"

    def test_no_identity_no_prefix(self, monkeypatch):
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        assert prefix_passage("plain text") == "plain text"

    def test_idempotent(self, monkeypatch):
        """An already-prefixed passage is never double-prefixed."""
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        once = prefix_passage("duties", "16")
        assert prefix_passage(once, "16") == once

    def test_force_bypasses_flag(self, monkeypatch):
        """force=True bakes the prefix even with the flag off (dataset path)."""
        monkeypatch.delenv("RAG_CE_SECTION_PREFIX", raising=False)
        assert prefix_passage("duties", "16", force=True) == "\u00a716 duties"

    def test_force_still_requires_identity(self):
        """force=True does not invent a prefix when no identity exists."""
        assert prefix_passage("plain text", force=True) == "plain text"


# --------------------------------------------------------------------------- #
# Serve-side wiring: Reranker + EnsembleReranker
# --------------------------------------------------------------------------- #


class _RecordingEncoder:
    """Cross-encoder stub that records the pairs it saw."""

    def __init__(self):
        self.seen: list[tuple[str, str]] = []

    def predict(self, pairs):
        self.seen.extend(pairs)
        # Score by presence of the section marker (prefixed chunks win)
        return [1.0 if "\u00a7" in text else 0.0 for _q, text in pairs]


def _chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id="c1", score=0.5, text="duties of the Authority", section_number="16"),
        RetrievedChunk(chunk_id="c2", score=0.4, text="penalties for misbranding", section_number="56"),
        RetrievedChunk(chunk_id="c3", score=0.3, text="registration of petty businesses", clause_number="2.1.1"),
        RetrievedChunk(chunk_id="c4", score=0.2, text="boilerplate header"),
    ]


class TestServeWiring:
    def test_reranker_prefixes_when_enabled(self, monkeypatch):
        from app.rag.retrieval.reranker import Reranker

        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        enc = _RecordingEncoder()
        reranker = Reranker(encoder=enc)
        reranker.rerank("duties", _chunks(), top_k=4)
        assert ("duties", "\u00a716 duties of the Authority") in enc.seen
        assert ("duties", "\u00a756 penalties for misbranding") in enc.seen
        assert ("duties", "\u00a72.1.1 registration of petty businesses") in enc.seen
        assert ("duties", "boilerplate header") in enc.seen  # no identity → unchanged

    def test_reranker_unprefixed_when_off(self, monkeypatch):
        from app.rag.retrieval.reranker import Reranker

        monkeypatch.delenv("RAG_CE_SECTION_PREFIX", raising=False)
        enc = _RecordingEncoder()
        reranker = Reranker(encoder=enc)
        reranker.rerank("duties", _chunks(), top_k=4)
        assert ("duties", "duties of the Authority") in enc.seen
        assert all("\u00a7" not in t for _q, t in enc.seen)

    def test_ensemble_head_prefixes_when_enabled(self, monkeypatch):
        from app.rag.retrieval.reranker import EnsembleReranker

        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        enc = _RecordingEncoder()
        ens = EnsembleReranker(encoder=enc, ce_head=20, ce_weight=0.5)
        ens.rerank("duties", _chunks(), top_k=4)
        assert any("\u00a716" in t for _q, t in enc.seen)

    def test_ensemble_head_unprefixed_when_off(self, monkeypatch):
        from app.rag.retrieval.reranker import EnsembleReranker

        monkeypatch.delenv("RAG_CE_SECTION_PREFIX", raising=False)
        enc = _RecordingEncoder()
        ens = EnsembleReranker(encoder=enc, ce_head=20, ce_weight=0.5)
        ens.rerank("duties", _chunks(), top_k=4)
        assert all("\u00a7" not in t for _q, t in enc.seen)

    def test_retrieved_chunk_carries_clause_number(self):
        """clause_number survives payload→chunk conversion (serve parity)."""
        chunk = RetrievedChunk.from_dict({
            "chunk_id": "x",
            "score": 1.0,
            "text": "t",
            "section_number": None,
            "clause_number": "2.4.15",
        })
        assert chunk.clause_number == "2.4.15"
        assert chunk.to_dict()["clause_number"] == "2.4.15"


# --------------------------------------------------------------------------- #
# Dataset: identity propagation + --section-prefix + --domain-balanced
# --------------------------------------------------------------------------- #


def _write_mining(tmp_path, monkeypatch):
    from evaluation import pairwise_dataset

    mining_file = tmp_path / "mining.jsonl"
    mining_file.write_text(
        json.dumps(
            {
                "question_id": "Q001",
                "query": "duties?",
                "gold_units": ["fssai:s16"],
                "positives": [
                    {
                        "chunk_id": "pos-a",
                        "text": "duties text",
                        "rank": 1,
                        "gold_unit": "fssai:s16",
                        "section": "16",
                        "act_name": "FSS Act",
                    },
                ],
                "negatives": [
                    {
                        "chunk_id": "neg-a",
                        "text": "penalties text",
                        "rank": 2,
                        "tier": 3,
                        "score": 5.0,
                        "features": {},
                        "section": "56",
                        "act_name": "FSS Act",
                    },
                    {
                        "chunk_id": "neg-b",
                        "text": "powers text",
                        "rank": 3,
                        "tier": 2,
                        "score": 3.0,
                        "features": {},
                        "section": "56",
                        "act_name": "FSS Act",
                    },
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("evaluation.pairwise_dataset.MINING_FILE", mining_file)
    # No payload index file → builder falls back to mining-record fields.
    monkeypatch.setattr("evaluation.pairwise_dataset.PAYLOAD_INDEX_FILE", tmp_path / "none.jsonl")
    return pairwise_dataset


class TestDatasetIdentity:
    def test_identity_metadata_propagated(self, tmp_path, monkeypatch):
        pd = _write_mining(tmp_path, monkeypatch)
        examples = pd.build_pairwise_examples(mode="uniform", max_negatives_per_tier=8)
        ex = examples[0]
        assert ex["positive_section"] == "16"
        assert ex["negative_section"] == "56"
        assert ex["positive_act"] == "FSS Act"
        # clause absent in mining + no payload index → None
        assert ex["positive_clause"] is None

    def test_payload_index_wins_over_mining_section(self, tmp_path, monkeypatch):
        """Authoritative payload index overrides stale mining section (P1)."""
        pd = _write_mining(tmp_path, monkeypatch)
        pi = tmp_path / "payload_index.jsonl"
        # pos-a's authoritative section is now 22 (post-L5/L7), neg-a is a
        # regulation clause (no section, clause 3.04).
        pi.write_text(
            json.dumps({
                "id": "pos-a",
                "payload": {"section_number": "22", "clause_number": None, "act_name": "FSS Act"},
            })
            + "\n"
            + json.dumps({
                "id": "neg-a",
                "payload": {"section_number": None, "clause_number": "3.04", "act_name": "FSS Regs"},
            })
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("evaluation.pairwise_dataset.PAYLOAD_INDEX_FILE", pi)
        examples = pd.build_pairwise_examples(mode="uniform", max_negatives_per_tier=8)
        pos_sections = {e["positive_section"] for e in examples}
        assert pos_sections == {"22"}  # payload index overrides stale mining "16"
        # neg-a resolves via the payload index (section None, clause 3.04);
        # neg-b is absent from the index and falls back to mining "56".
        by_neg = {e["neg_chunk_id"]: e for e in examples}
        assert by_neg["neg-a"]["negative_section"] is None
        assert by_neg["neg-a"]["negative_clause"] == "3.04"
        assert by_neg["neg-b"]["negative_section"] == "56"

    def test_section_prefix_bakes_identity(self, tmp_path, monkeypatch):
        """--section-prefix bakes the prefix even with the serve flag off."""
        pd = _write_mining(tmp_path, monkeypatch)
        monkeypatch.delenv("RAG_CE_SECTION_PREFIX", raising=False)
        examples = pd.build_pairwise_examples(mode="uniform", max_negatives_per_tier=8, section_prefix=True)
        assert all(e["positive"].startswith("\u00a716 ") for e in examples)
        assert all(e["negative"].startswith("\u00a756 ") for e in examples)

    def test_no_prefix_without_flag(self, tmp_path, monkeypatch):
        pd = _write_mining(tmp_path, monkeypatch)
        monkeypatch.setenv("RAG_CE_SECTION_PREFIX", "true")
        examples = pd.build_pairwise_examples(mode="uniform", max_negatives_per_tier=8, section_prefix=False)
        assert all(e["positive"] == "duties text" for e in examples)
        assert all("\u00a7" not in e["negative"] for e in examples)


class TestDomainBalance:
    def test_oversamples_underrepresented_domains(self):
        import random

        from evaluation.pairwise_dataset import _balance_domains

        rng = random.Random(42)
        examples = (
            [{"gold_unit": "fssai:s16", "question_id": "Q1"} for _ in range(10)]
            + [{"gold_unit": "srf:s10", "question_id": "Q2"} for _ in range(2)]
            + [{"gold_unit": "epa:s6", "question_id": "Q3"} for _ in range(4)]
        )
        # cap=None → full equalization to the largest domain (10)
        balanced = _balance_domains(examples, rng, cap=None)
        from collections import Counter

        doms = Counter(str(e["gold_unit"]).split(":", 1)[0] for e in balanced)
        assert doms["fssai"] == 10
        assert doms["srf"] == 10
        assert doms["epa"] == 10
        assert len(balanced) == 30

    def test_cap_bounds_oversampling(self):
        """cap=3.0 limits each domain to 3× its own count."""
        import random

        from evaluation.pairwise_dataset import _balance_domains

        rng = random.Random(1)
        examples = [{"gold_unit": "fssai:s16", "question_id": "Q1"} for _ in range(10)] + [
            {"gold_unit": "srf:s10", "question_id": "Q2"} for _ in range(2)
        ]
        balanced = _balance_domains(examples, rng, cap=3.0)
        from collections import Counter

        doms = Counter(str(e["gold_unit"]).split(":", 1)[0] for e in balanced)
        assert doms["fssai"] == 10  # already the largest — unchanged
        assert doms["srf"] == 6  # 2 × 3.0 cap
        assert len(balanced) == 16

    def test_preserves_existing_when_already_balanced(self):
        import random

        from evaluation.pairwise_dataset import _balance_domains

        rng = random.Random(1)
        examples = [{"gold_unit": "fssai:s16", "question_id": "Q1"} for _ in range(5)] + [
            {"gold_unit": "epa:s6", "question_id": "Q2"} for _ in range(5)
        ]
        balanced = _balance_domains(examples, rng, cap=3.0)
        assert len(balanced) == 10
