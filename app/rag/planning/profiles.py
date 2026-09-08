"""Named query and retrieval profiles for configurable system behavior.

Profiles define:
- Query decomposition strategy
- Retrieval parameters
- Evidence optimization thresholds
- Reranking weights
- Retry behavior
- KG reasoning preferences

ponytail: minimal profiles, one-line shortcuts, delegate defaults.
Upgrade path: richer profile languages, runtime profile editing.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryProfile:
    """Profile for query decomposition and classification behavior."""

    name: str
    # Decomposition behavior
    max_depth: int = 3
    strategy: str = "hierarchical"  # hierarchical, keyword, semantic
    # Evidence requirements per query type
    evidence_thresholds: dict[str, float] = field(default_factory=dict)
    # Reranking weights
    rerank_weights: dict[str, float] = field(
        default_factory=lambda: {
            "legal_identity": 0.4,
            "legal_ranker": 0.4,
            "ce_reranker": 0.2,
        }
    )
    # Retrieval behavior
    default_top_k: int = 10
    enable_hybrid: bool = True
    enable_ggt: bool = True
    enable_identifier: bool = True
    # Retry behavior
    max_retries: int = 2
    targeted_retry_enabled: bool = True
    # KG reasoning
    kg_reasoning_enabled: bool = True
    kg_max_depth: int = 2
    # Coverage optimization
    coverage_target: float = 0.8
    coverage_timeout: int = 60

    def __post_init__(self) -> None:
        """Set defaults for missing thresholds."""
        if not self.evidence_thresholds:
            self.evidence_thresholds = {
                "definition": 0.9,
                "lookup": 0.8,
                "prohibition": 0.8,
                "power": 0.7,
                "penalty": 0.7,
                "exception": 0.7,
                "procedure": 0.6,
                "applicability": 0.6,
                "comparison": 0.7,
                "temporal": 0.8,
                "jurisdiction": 0.8,
                "cross_reference": 0.8,
                "multi_hop": 0.8,
                "fact_pattern": 0.6,
                "compliance_assessment": 0.7,
                "identification": 0.8,
            }


@dataclass
class RetrievalProfile:
    """Profile for retrieval component behavior."""

    name: str
    # Vector retrieval
    vector_top_k: int = 10
    vector_threshold: float = 0.7
    vector_model: str = "text-embedding-3-small"
    # Keyword retrieval
    keyword_top_k: int = 20
    keyword_threshold: float = 0.8
    # Identifier retrieval
    identifier_top_k: int = 5
    # Hybrid aggregation
    hybrid_weight: float = 0.6
    # Reranking
    rerank_top_k: int = 20
    rerank_threshold: float = 0.5
    # Evidence optimization
    coverage_weight: float = 0.7
    diversity_weight: float = 0.3
    # Temporal filtering
    temporal_enabled: bool = True
    temporal_window_days: int = 365
    # Authority filtering
    authority_enabled: bool = True
    # Retry parameters
    retry_attempts: int = 3
    retry_delay_ms: int = 1000
    # Cache
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600


class ProfileManager:
    """Manages named profiles for query planning and retrieval."""

    def __init__(self) -> None:
        self._query_profiles: dict[str, QueryProfile] = {}
        self._retrieval_profiles: dict[str, RetrievalProfile] = {}
        self._load_default_profiles()

    def _load_default_profiles(self) -> None:
        """Load built-in profiles."""
        # Standard profile - balanced across all factors
        self._query_profiles["standard"] = QueryProfile(
            name="standard",
            max_depth=3,
            evidence_thresholds={"definition": 0.9, "lookup": 0.8, "prohibition": 0.8},
        )

        # Fast profile - shallow decomposition, fewer retrievals
        self._query_profiles["fast"] = QueryProfile(
            name="fast",
            max_depth=2,
            evidence_thresholds={"definition": 0.7, "lookup": 0.6, "prohibition": 0.6},
            default_top_k=5,
            max_retries=1,
            kg_reasoning_enabled=False,
        )

        # Deep profile - thorough decomposition, comprehensive retrieval
        self._query_profiles["deep"] = QueryProfile(
            name="deep",
            max_depth=5,
            evidence_thresholds={"definition": 0.95, "lookup": 0.9, "prohibition": 0.9},
            default_top_k=20,
            max_retries=3,
            kg_reasoning_enabled=True,
            coverage_target=0.95,
        )

        # Legal-specific profile - optimized for legal domains
        self._query_profiles["legal"] = QueryProfile(
            name="legal",
            max_depth=4,
            evidence_thresholds={
                "definition": 0.9,
                "lookup": 0.8,
                "prohibition": 0.8,
                "penalty": 0.7,
                "authority": 0.8,
            },
            rerank_weights={
                "legal_identity": 0.5,
                "legal_ranker": 0.4,
                "ce_reranker": 0.1,
            },
            kg_reasoning_enabled=True,
            kg_max_depth=3,
        )

        # Retrieval profiles
        self._retrieval_profiles["standard"] = RetrievalProfile(
            name="standard",
            vector_top_k=10,
            vector_threshold=0.7,
            keyword_top_k=20,
            hybrid_weight=0.6,
        )

        self._retrieval_profiles["fast"] = RetrievalProfile(
            name="fast",
            vector_top_k=5,
            vector_threshold=0.5,
            keyword_top_k=10,
            hybrid_weight=0.5,
            rerank_top_k=10,
            cache_enabled=True,
        )

        self._retrieval_profiles["deep"] = RetrievalProfile(
            name="deep",
            vector_top_k=20,
            vector_threshold=0.8,
            keyword_top_k=50,
            hybrid_weight=0.7,
            rerank_top_k=50,
            temporal_enabled=True,
            authority_enabled=True,
        )

        self._retrieval_profiles["legal"] = RetrievalProfile(
            name="legal",
            vector_top_k=15,
            vector_threshold=0.7,
            keyword_top_k=30,
            hybrid_weight=0.6,
            rerank_top_k=30,
            temporal_enabled=True,
            authority_enabled=True,
        )

    def get_query_profile(self, name: str) -> QueryProfile:
        """Get a query profile by name."""
        if name not in self._query_profiles:
            raise ValueError(f"Unknown query profile: {name}")
        return self._query_profiles[name]

    def get_retrieval_profile(self, name: str) -> RetrievalProfile:
        """Get a retrieval profile by name."""
        if name not in self._retrieval_profiles:
            raise ValueError(f"Unknown retrieval profile: {name}")
        return self._retrieval_profiles[name]

    def register_query_profile(self, profile: QueryProfile) -> None:
        """Register a new query profile."""
        self._query_profiles[profile.name] = profile

    def register_retrieval_profile(self, profile: RetrievalProfile) -> None:
        """Register a new retrieval profile."""
        self._retrieval_profiles[profile.name] = profile

    def list_query_profiles(self) -> list[str]:
        """List all registered query profile names."""
        return list(self._query_profiles.keys())

    def list_retrieval_profiles(self) -> list[str]:
        """List all registered retrieval profile names."""
        return list(self._retrieval_profiles.keys())
