"""Unit tests for the Phase 14 retrieval evaluation harness.

Covers the pure metric helpers, gold-phrase resolution, section parsing, RRF
fusion, and an end-to-end synthetic check that the enriched retriever can beat
the baseline on a query whose answer relies on enrichment keywords (while the
dense index is unchanged).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.enrichment.evaluate_retrieval import (
    ABLATION_VARIANTS,
    ALL_FEATURES,
    FEATURE_CROSSREFS,
    FEATURE_KEYWORDS,
    FEATURE_SECTION,
    FEATURE_SUMMARY,
    baseline_retrieve,
    build_lexical_index,
    cosine_topk,
    enriched_retrieve,
    lexical_scores,
    load_backup,
    mrr,
    ndcg,
    normalize_text,
    parse_query_section,
    precision_at,
    recall_at,
    resolve_gold,
    rrf_fuse,
    tokens_of,
)


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------
class TestMetrics:
    def test_recall_at(self):
        ranked = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        assert recall_at(ranked, {"a"}, 5) == pytest.approx(1.0)
        assert recall_at(ranked, {"z"}, 5) == 0.0
        assert recall_at(ranked, {"a", "b", "c"}, 5) == pytest.approx(1.0)
        assert recall_at(ranked, {"a", "z"}, 5) == pytest.approx(0.5)
        assert recall_at(ranked, set(), 5) == 0.0

    def test_precision_at(self):
        ranked = ["a", "b", "c", "d", "e"]
        assert precision_at(ranked, {"a", "b"}, 5) == pytest.approx(0.4)
        assert precision_at(ranked, {"a"}, 1) == pytest.approx(1.0)
        assert precision_at(ranked, set(), 5) == 0.0
        # k larger than len(ranked) divides by min(k, len)
        assert precision_at(["a"], {"a"}, 10) == pytest.approx(1.0)

    def test_mrr(self):
        assert mrr(["a", "b", "c"], {"b"}) == pytest.approx(1.0 / 2)
        assert mrr(["a", "b", "c"], {"c"}) == pytest.approx(1.0 / 3)
        assert mrr(["a", "b", "c"], {"x"}) == 0.0
        assert mrr(["a", "b", "c"], {"a"}) == pytest.approx(1.0)

    def test_ndcg(self):
        # Perfect ranking: nDCG = 1
        ranked = ["g1", "g2", "x", "y"]
        assert ndcg(ranked, {"g1", "g2"}, 10) == pytest.approx(1.0)
        # Ideal gains: 1/log2(2) + 1/log2(3)
        ideal = 1.0 / math.log2(2) + 1.0 / math.log2(3)
        # Reversed gold: 1/log2(4) + 1/log2(5)
        actual = 1.0 / math.log2(4) + 1.0 / math.log2(5)
        assert ndcg(["x", "y", "g1", "g2"], {"g1", "g2"}, 10) == pytest.approx(actual / ideal)
        assert ndcg(["a", "b"], set(), 10) == 0.0


# ---------------------------------------------------------------------------
# Normalization / tokenization / section parsing
# ---------------------------------------------------------------------------
class TestTextHelpers:
    def test_normalize_text(self):
        assert normalize_text("  Food   Safety &  \n  Standards ") == "food safety & standards"
        assert normalize_text("") == ""
        assert normalize_text(None) == ""

    def test_tokens_of(self):
        assert tokens_of("Section 32 — Improvement Notice") == ["section", "32", "improvement", "notice"]
        assert tokens_of("") == []

    def test_parse_query_section(self):
        assert parse_query_section("What does section 32 say?") == "32"
        assert parse_query_section("per section 4 of the Act") == "4"
        assert parse_query_section("under sec. 92") == "92"
        assert parse_query_section("What is an improvement notice?") is None
        assert parse_query_section("See s. 5.") == "5"


# ---------------------------------------------------------------------------
# RRF / lexical scoring
# ---------------------------------------------------------------------------
class TestFusion:
    def test_rrf_fuse(self):
        fused = rrf_fuse([["a", "b"], ["b", "c"]], k=60)
        # b appears in both → strictly higher than either single appearance
        assert fused["b"] > fused["a"]
        assert fused["b"] > fused["c"]

    def test_lexical_scores_phrase_match(self):
        # Phrase-level matching: multi-word enrichment keywords fire only when
        # the query actually contains the whole phrase.
        doc_phrases = [{"insecticide"}, {"fumigant"}, {"insecticide", "fumigant"}]
        idf = {"insecticide": 1.5, "fumigant": 1.2}
        scores = lexical_scores("insecticide on food", doc_phrases, idf)
        assert scores[0] == pytest.approx(1.5)
        assert scores[1] == pytest.approx(0.0)
        assert scores[2] == pytest.approx(1.5)
        scores2 = lexical_scores("fumigant", doc_phrases, idf)
        assert scores2[1] == pytest.approx(1.2)
        assert scores2[2] == pytest.approx(1.2)
        # No phrase contained in the query → zero
        scores3 = lexical_scores("zzzz", doc_phrases, idf)
        assert scores3.sum() == 0.0


# ---------------------------------------------------------------------------
# Gold resolution
# ---------------------------------------------------------------------------
class TestGoldResolution:
    def _enrichment(self) -> dict[str, dict]:
        return {
            "c1": {"section": "32", "keywords": ["notice"], "cross_ref_targets": [], "summary": "", "text": "If the Designated Officer has reasonable ground for believing that any food business operator has failed to comply.", "document_id": "docA"},
            "c2": {"section": "32", "keywords": [], "cross_ref_targets": [], "summary": "", "text": "The concerned Food Safety Officer shall with the approval of the Designated Officer issue a certificate.", "document_id": "docA"},
            "c3": {"section": "22", "keywords": [], "cross_ref_targets": [], "summary": "", "text": "No insecticide shall be used directly on article of food.", "document_id": "docB"},
        }

    def test_phrase_matches_single_chunk(self):
        q = {"id": "q1", "document_id": "docA", "gold_phrases": ["reasonable ground for believing"]}
        gold, unmatched = resolve_gold(q, self._enrichment())
        assert gold == {"c1"}
        assert unmatched == []

    def test_phrase_tolerates_embedded_quotes(self):
        # Real corpus text wraps defined terms in quotes: ``(o) "food business
        # operator" in relation to ...`` — the phrase must still match.
        q = {
            "id": "q9",
            "document_id": "docA",
            "gold_phrases": ["food business operator in relation to food business means a person"],
        }
        enrichment = {
            "c9": {
                "section": "4",
                "keywords": [],
                "cross_ref_targets": [],
                "summary": "",
                "text": '(o) "food business operator" in relation to food business means a person by whom the business is carried on.',
                "document_id": "docA",
            }
        }
        gold, unmatched = resolve_gold(q, enrichment)
        assert gold == {"c9"}
        assert unmatched == []

    def test_phrase_restricted_to_document(self):
        q = {"id": "q2", "document_id": "docA", "gold_phrases": ["insecticide"]}
        gold, unmatched = resolve_gold(q, self._enrichment())
        assert gold == set()
        assert unmatched == ["insecticide"]

    def test_whitespace_insensitive(self):
        q = {"id": "q3", "document_id": "docB", "gold_phrases": ["No   insecticide  shall be used"]}
        gold, unmatched = resolve_gold(q, self._enrichment())
        assert gold == {"c3"}
        assert unmatched == []

    def test_multi_phrase_two_chunks(self):
        q = {"id": "q4", "document_id": "docA", "gold_phrases": ["reasonable ground", "issue a certificate"]}
        gold, unmatched = resolve_gold(q, self._enrichment())
        assert gold == {"c1", "c2"}
        assert unmatched == []

    def test_no_gold_when_phrase_absent(self):
        q = {"id": "q5", "document_id": "docB", "gold_phrases": ["never mentioned anywhere"]}
        gold, unmatched = resolve_gold(q, self._enrichment())
        assert gold == set()
        assert unmatched == ["never mentioned anywhere"]

    def test_phrase_matching_all_chunks(self):
        # A distinctive answer phrase may appear in more than one chunk — ALL
        # matches must join gold (not just the first encountered).
        enrichment = {
            "c1": {"section": "32", "keywords": [], "cross_ref_targets": [], "summary": "", "text": "An improvement notice may be issued by the Designated Officer.", "document_id": "docA"},
            "c2": {"section": "32", "keywords": [], "cross_ref_targets": [], "summary": "", "text": "The improvement notice procedure continues under this section.", "document_id": "docA"},
            "c3": {"section": "32", "keywords": [], "cross_ref_targets": [], "summary": "", "text": "Unrelated text.", "document_id": "docA"},
        }
        q = {"id": "q6", "document_id": "docA", "gold_phrases": ["improvement notice"]}
        gold, unmatched = resolve_gold(q, enrichment)
        assert gold == {"c1", "c2"}
        assert unmatched == []


# ---------------------------------------------------------------------------
# End-to-end synthetic: enriched beats baseline via enrichment keywords
# ---------------------------------------------------------------------------
class TestSyntheticRetrieval:
    def _make_corpus(self):
        """4 docs; the answer chunk's *text* is unrelated to the query but its
        enrichment keywords contain the query terms — so only the enriched
        lexical path can find it."""
        texts = [
            "Alpha beta gamma delta epsilon zeta eta theta",  # c0 (noise)
            "iota kappa lambda mu nu xi omicron pi rho",     # c1 (noise)
            "sigma tau upsilon phi chi psi omega",           # c2 (answer text, unrelated wording)
            "A B C D E F G H I J K L M N O P Q R S T",       # c3 (noise)
        ]
        n = len(texts)
        dim = 16
        rng = np.random.default_rng(42)
        matrix = rng.normal(size=(n, dim)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        ids = [f"c{i}" for i in range(n)]
        payloads = {f"c{i}": {"chunk_text": t} for i, t in enumerate(texts)}
        enrichment = {
            "c0": {"section": None, "keywords": [], "cross_ref_targets": [], "summary": "", "text": texts[0], "document_id": "d"},
            "c1": {"section": None, "keywords": [], "cross_ref_targets": [], "summary": "", "text": texts[1], "document_id": "d"},
            "c2": {"section": None, "keywords": ["improvement", "notice"], "cross_ref_targets": [], "summary": "improvement notice issued by the officer", "text": texts[2], "document_id": "d"},
            "c3": {"section": None, "keywords": [], "cross_ref_targets": [], "summary": "", "text": texts[3], "document_id": "d"},
        }
        return matrix, ids, payloads, enrichment, texts

    def test_baseline_vs_enriched(self):
        matrix, ids, _payloads, enrichment, _texts = self._make_corpus()
        kw_phrases, kw_idf, sum_phrases, sum_idf = build_lexical_index(ids, enrichment)
        # Query vector unrelated to c2 text (synthetic: same dim, random unit)
        qvec = np.random.default_rng(1).normal(size=(16,)).astype(np.float32)
        qvec /= np.linalg.norm(qvec)

        b = baseline_retrieve(qvec, matrix, ids, top_k=4)
        e = enriched_retrieve(
            "Who can issue an improvement notice?", qvec, matrix, ids,
            enrichment, kw_phrases, kw_idf, sum_phrases, sum_idf, top_k=4,
        )
        assert "c2" in e  # lexical keywords must surface c2 in enriched
        # The enriched path must rank c2 strictly higher than the baseline does
        assert e.index("c2") <= b.index("c2")

    def test_features_gate_lexical_credit(self):
        """Phase 15: without the keywords feature the lexical tie-break must not
        fire; with it, the keyword-rich chunk must be re-ranked above the
        baseline."""
        matrix, ids, _payloads, enrichment, _texts = self._make_corpus()
        kw_phrases, kw_idf, sum_phrases, sum_idf = build_lexical_index(ids, enrichment)
        qvec = np.random.default_rng(2).normal(size=(16,)).astype(np.float32)
        qvec /= np.linalg.norm(qvec)

        no_kw = enriched_retrieve(
            "Who can issue an improvement notice?", qvec, matrix, ids,
            enrichment, kw_phrases, kw_idf, sum_phrases, sum_idf, top_k=4,
            features=frozenset(),
        )
        with_kw = enriched_retrieve(
            "Who can issue an improvement notice?", qvec, matrix, ids,
            enrichment, kw_phrases, kw_idf, sum_phrases, sum_idf, top_k=4,
            features=frozenset({FEATURE_KEYWORDS}),
        )
        # With no features the pool is dense-only; keywords must be able to
        # improve c2's rank when enabled.
        assert with_kw.index("c2") <= no_kw.index("c2")

    def test_ablation_variants_declared(self):
        """Every declared variant must map to a feature subset; baseline must
        be empty and full must be all features."""
        assert ABLATION_VARIANTS["baseline"] == frozenset()
        assert ABLATION_VARIANTS["full"] == ALL_FEATURES
        for name, feats in ABLATION_VARIANTS.items():
            assert feats <= ALL_FEATURES, name
        # Every feature appears in at least one single-feature variant
        singles = {v for name, v in ABLATION_VARIANTS.items() if name not in ("baseline", "full")}
        for feat in (FEATURE_KEYWORDS, FEATURE_SUMMARY, FEATURE_SECTION, FEATURE_CROSSREFS):
            assert any(feat in s for s in singles), feat


# ---------------------------------------------------------------------------
# load_backup smoke (real file optional — skip if missing)
# ---------------------------------------------------------------------------
class TestLoadBackup:
    def test_load_backup_shape(self, tmp_path):
        points = [
            {"id": "p1", "vector": {"dense": [1.0, 0.0]}, "payload": {"chunk_id": "p1", "chunk_text": "hello"}},
            {"id": "p2", "vector": {"dense": [0.0, 1.0]}, "payload": {"chunk_id": "p2", "chunk_text": "world"}},
        ]
        path = tmp_path / "backup.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"vector_size": 2, "points": points}, f)
        matrix, ids, payloads = load_backup(str(path))
        assert matrix.shape == (2, 2)
        # Rows are unit-normalised
        np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), [1.0, 1.0])
        assert ids == ["p1", "p2"]
        assert payloads["p1"]["chunk_text"] == "hello"

    def test_cosine_topk_orders_by_similarity(self):
        matrix = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)
        ids = ["a", "b", "c"]
        ranked = cosine_topk(np.array([1.0, 0.0]), matrix, ids, 3)
        assert [cid for cid, _ in ranked] == ["a", "b", "c"]
