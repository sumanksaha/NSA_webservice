"""2.4 — Three-Stage Reranking (Intelligence Layer).

Legal identity features → deterministic legal ranker → CE → diversity ranker.
Replaces the two-stage (RRF → CE) approach with legal-aware reranking.

ponytail: minimal version — deterministic weights and stages.
Upgrade path: learned reranking policy if validation shows gains.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.retrieval.result import RetrievedChunk


@dataclass
class RerankScore:
    """Component scores for explainable reranking."""

    identity: float = 0.0  # legal identity match (exact section/act)
    legal_rank: float = 0.0  # deterministic legal features (proximity, hierarchy)
    ce_score: float = 0.0  # cross-encoder semantic relevance
    diversity: float = 0.0  # diversity penalty (negative = penalize similarity)
    total: float = 0.0  # final weighted score


@dataclass
class LegalRankerConfig:
    """Weights for deterministic legal ranker."""

    section_proximity: float = 0.4  # closer sections score higher
    authority_hierarchy: float = 0.3  # higher authorities score higher
    provision_type: float = 0.3  # penalty/authority/deficiency weights


class ThreeStageReranker:
    """Three-stage reranking pipeline for legal queries."""

    def __init__(self, legal_weights: LegalRankerConfig | None = None) -> None:
        self.legal_weights = legal_weights or LegalRankerConfig()
        # Lazy-load CE encoder (same as EnsembleReranker)
        self._ce_encoder = None

    def _get_ce_encoder(self):
        """Lazy-load cross-encoder."""
        if self._ce_encoder is None:
            from app.rag.retrieval.reranker import EnsembleReranker

            # Reuse the CE loading logic from EnsembleReranker
            self._ce_encoder = EnsembleReranker()._get_encoder()
        return self._ce_encoder

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int = 10,
        query_type: str = "general",
    ) -> list[RetrievedChunk]:
        """Three-stage reranking: identity → legal ranker → CE → diversity."""
        if not chunks:
            return chunks

        # Stage 1: Legal identity / metadata filter
        identity_scores = self._score_legal_identity(chunks, query)
        # Stage 2: Deterministic legal ranker (section proximity, authority hierarchy)
        legal_scores = self._score_legal_features(chunks, query_type)
        # Combine identity + legal for Stage 1+2 output
        stage12_scores = [(identity + legal) / 2 for identity, legal in zip(identity_scores, legal_scores)]

        # Stage 3: Cross-encoder on top N from stage 1+2
        # Take top 2x for CE reranking (configurable)
        ce_candidates = sorted(enumerate(stage12_scores), key=lambda x: x[1], reverse=True)[
            : min(len(chunks), top_k * 2)
        ]
        ce_indices = [idx for idx, _ in ce_candidates]
        ce_chunks = [chunks[i] for i in ce_indices]

        # Get CE scores for candidates
        ce_scores_raw = self._score_cross_encoder(query, ce_chunks)
        ce_scores = [0.0] * len(chunks)
        for idx, score in zip(ce_indices, ce_scores_raw, strict=False):
            ce_scores[idx] = score

        # Stage 4: Diversity / coverage ranker
        diversity_scores = self._score_diversity(chunks, ce_indices)

        # Final weighted combination
        final_scores = []
        for i in range(len(chunks)):
            identity = identity_scores[i]
            legal = legal_scores[i]
            ce = ce_scores[i]
            diversity = diversity_scores[i]

            # Weighted sum: identity + legal + CE - diversity_penalty
            total = (
                0.3 * identity + 0.3 * legal + 0.3 * ce - 0.1 * max(0.0, diversity)  # only penalize if diversity > 0
            )
            final_scores.append(total)

        # Sort by final score and return top_k
        scored_chunks = list(zip(final_scores, chunks, strict=False))
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return [chunk for _, chunk in scored_chunks[:top_k]]

    def _score_legal_identity(self, chunks: list[RetrievedChunk], query: str) -> list[float]:
        """Score exact legal identity matches (act + section)."""
        from app.rag.retrieval.query_classifier import QueryClassifier

        classifier = QueryClassifier()
        query_type = classifier.classify(query)

        scores = []
        for chunk in chunks:
            score = 0.0
            metadata = chunk.metadata or {}

            # Exact act match
            if query_type in [
                QueryClassifier.QueryType.SECTION_LOOKUP,
                QueryClassifier.QueryType.IDENTIFICATION,
                QueryClassifier.QueryType.PENALTY,
                QueryClassifier.QueryType.AUTHORITY,
            ]:
                act_match = metadata.get("act", "").lower() in query.lower()
                section_match = str(metadata.get("section", "")) in query
                if act_match and section_match:
                    score = 1.0
                elif act_match or section_match:
                    score = 0.5

            scores.append(score)
        return scores

    def _score_legal_features(self, chunks: list[RetrievedChunk], query_type: str) -> list[float]:
        """Deterministic legal ranker: section proximity, authority hierarchy."""
        scores = []
        for chunk in chunks:
            score = 0.0
            metadata = chunk.metadata or {}

            # Section proximity: closer to referenced sections score higher
            section_num = metadata.get("section")
            if section_num and section_num.isdigit():
                # Simple heuristic: lower section numbers often more fundamental
                # In practice, this would be query-dependent
                try:
                    num = int(section_num)
                    score += max(0.0, 1.0 - (num - 1) / 100)  # normalize 1-100
                except ValueError:
                    pass

            # Authority hierarchy: higher authorities score higher
            authority = metadata.get("authority", "").lower()
            if "supreme court" in authority:
                score += 1.0
            elif "high court" in authority:
                score += 0.7
            elif "ministry" in authority or "government" in authority:
                score += 0.5

            scores.append(min(1.0, score))  # cap at 1.0
        return scores

    def _score_cross_encoder(self, query: str, chunks: list[RetrievedChunk]) -> list[float]:
        """Cross-encoder semantic relevance (lazy-loaded)."""
        encoder = self._get_ce_encoder()
        if not encoder:
            return [0.5] * len(chunks)  # fallback

        try:
            # Prepare pairs for CE: [query, chunk_text]
            pairs = [[query, chunk.text[:512]] for chunk in chunks]
            scores = encoder.predict(pairs)
            # Normalize to 0-1 range (CE scores can be any real)
            import numpy as np

            if len(scores) > 0:
                scores = np.array(scores)
                scores = (scores - scores.min()) / (scores.max() - scores.min() + 1e-8)
            return scores.tolist()
        except Exception:
            return [0.5] * len(chunks)  # fallback on error

    def _score_diversity(self, chunks: list[RetrievedChunk], ce_indices: list[int]) -> list[float]:
        """Diversity penalty: chunks too similar to already-selected ones get penalized."""
        # Simple diversity: penalize chunks from same section as high-scoring CE chunks
        selected_sections = set()
        for idx in ce_indices[:3]:  # top 3 CE chunks
            if idx < len(chunks):
                section = chunks[idx].metadata.get("section", "")
                selected_sections.add(section)

        scores = []
        for chunk in chunks:
            section = chunk.metadata.get("section", "")
            # Penalty increases with similarity to selected sections
            if section in selected_sections:
                scores.append(1.0)  # high penalty for duplicate section
            else:
                scores.append(0.0)  # no penalty
        return scores
