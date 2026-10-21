"""cfg — the single configuration seam for feature flags and settings.

One deep module owning:

* the resolution rule (**Pattern A**): inside a Flask app context,
  ``current_app.config`` wins; outside an app context, ``os.environ`` is
  read; otherwise the declared default applies. Env vars are seeded into
  config at startup by :func:`seed_config_from_env`, so in-context callers
  see the same values out-of-context callers get from env directly.
* one **declaration table**: every flag is declared exactly once with its
  key, type, default, boolean convention, and description. Adding a flag is
  one table row; the table doubles as living documentation of the config
  surface (see ``tests/test_shared_config.py`` for the docs-parity meta-test).

This module replaces the ~30 hand-rolled ``try: current_app.config /
except: os.environ`` resolvers that previously lived in ``app/rag/tasks.py``,
``app/rag/agent/graph.py``, ``app/api/deps.py``, the retrieval modules, and
``app/plugins/registry.py`` — each with subtly different defaults and parse
rules. Boolean conventions are preserved per-flag and declared explicitly:
``opt_in`` means the string must be ``"true"`` (so ``"1"``/``"yes"`` are
off); ``opt_out`` means anything but ``"false"`` is on.

Usage::

    from app.shared.config import cfg

    if cfg.kg_fusion:          # named accessor — declared in the table
        ...
    model = cfg.reranker_model
    val = cfg.get_str("SOME_DYNAMIC_KEY", default="x")   # rare dynamic keys
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Setting", "cfg", "seed_config_from_env"]


@dataclass(frozen=True)
class Setting:
    """One declared configuration setting.

    Attributes:
        key: Environment / Flask-config key (e.g. ``RAG_KG_FUSION``).
        attr: ``cfg`` attribute name (e.g. ``kg_fusion``).
        type: Value type — ``bool``, ``int``, ``float``, or ``str``.
        default: Value when neither config nor env provides one.
        opt_in: Boolean convention. ``True`` (opt-in): the raw string must
            be ``"true"`` to enable. ``False`` (opt-out): any string except
            ``"false"`` enables. Ignored for non-bool types.
        help: One-line description (kept for documentation/introspection).

    """

    key: str
    attr: str
    type: type
    default: Any
    opt_in: bool = True
    help: str = field(default="", compare=False)


#: The declaration table — the single source of truth for every setting.
_TABLE: tuple[Setting, ...] = (
    # --- RAG module switches -------------------------------------------------
    Setting(
        "RAG_ENABLED",
        "rag_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_USE_AGENT_PIPELINE",
        "use_agent_pipeline",
        bool,
        False,
        help="",
    ),
    Setting("RAG_AGENT_HITL", "agent_hitl", bool, False, help="M5 human-in-the-loop review interrupt before finalize."),
    Setting(
        "RAG_AGENT_CHECKPOINTER",
        "agent_checkpointer",
        str,
        "memory",
        help="",
    ),
    Setting(
        "RAG_HALLUCINATION_DETECTOR",
        "hallucination_detector",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    # --- Grounded answer channel ------------------------------------------------
    Setting(
        "RAG_LLM_MAX_TOKENS",
        "llm_max_tokens",
        int,
        4096,
        help="",
    ),
    Setting(
        "RAG_LLM_REASONING_MAX_TOKENS",
        "llm_reasoning_max_tokens",
        int,
        1024,
        help="",
    ),
    Setting(
        "RAG_LLM_TIMEOUT_S",
        "llm_timeout_s",
        float,
        30.0,
        help="",
    ),
    Setting(
        "RAG_LLM_REASONING_FALLBACK",
        "llm_reasoning_fallback",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_LLM_VERIFY_TLS",
        "llm_verify_tls",
        bool,
        True,
        opt_in=False,
        help="Verify LLM endpoint TLS; set false only for self-signed/private CA.",
    ),
    # --- Reinforcement learning (Phase 4: RL-augmented retrieval) ---------------
    Setting(
        "RAG_RL_ENABLED",
        "rl_enabled",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_RL_EXPLORATION_EPSILON",
        "rl_exploration_epsilon",
        float,
        0.1,
        help="",
    ),
    Setting(
        "RAG_RL_LEARNING_RATE",
        "rl_learning_rate",
        float,
        0.05,
        help="",
    ),
    Setting(
        "RAG_RL_MIN_OBSERVATIONS",
        "rl_min_observations",
        int,
        10,
        help="",
    ),
    Setting(
        "RAG_RL_OFFLINE_TRAIN",
        "rl_offline_train",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_RL_REWARD_FAITHFULNESS_WEIGHT",
        "rl_reward_faithfulness_weight",
        float,
        0.35,
        help="",
    ),
    Setting(
        "RAG_RL_REWARD_GROUNDEDNESS_WEIGHT",
        "rl_reward_groundedness_weight",
        float,
        0.35,
        help="",
    ),
    Setting(
        "RAG_RL_REWARD_CITATION_RECALL_WEIGHT",
        "rl_reward_citation_recall_weight",
        float,
        0.20,
        help="",
    ),
    Setting(
        "RAG_RL_REWARD_LATENCY_WEIGHT",
        "rl_reward_latency_weight",
        float,
        0.10,
        help="",
    ),
    # --- Retrieval pipeline flags -------------------------------------------
    Setting(
        "RAG_RETRIEVAL_CACHE",
        "retrieval_cache",
        bool,
        True,
        help="",
    ),
    Setting(
        "RAG_QDRANT_BM25",
        "qdrant_bm25",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_LEGAL_QUERY_TYPING",
        "legal_query_typing",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_IDENTIFIER_ROUTE",
        "identifier_route",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_SECTION_ARM",
        "section_arm",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_FORM_ARM",
        "form_arm",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_EVIDENCE_SELECTOR",
        "evidence_selector",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_STRUCTURED_REASONER",
        "structured_reasoner",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_QUERY_TYPE_DIRECTIVES",
        "query_type_directives",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_STRUCTURED_QUERY_TYPE_DIRECTIVES",
        "structured_query_type_directives",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_ANSWER_CONTRACT",
        "answer_contract",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_ANSWER_CONTRACT_REPAIR",
        "answer_contract_repair",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_LEGAL_AUDITOR",
        "legal_auditor",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_REFERENCE_EXPANSION",
        "reference_expansion",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_EVIDENCE_PLAN",
        "evidence_plan",
        bool,
        False,
        help="",
    ),
    # --- Decision pathway (alternate, default off; plan: rag-decision-pathway) ---
    Setting(
        "RAG_DECISION_PATH",
        "decision_path",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_DECISION_SHADOW",
        "decision_shadow",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_DECISION_MODEL",
        "decision_model",
        str,
        "typesafe/jev-1.13",
        help="",
    ),
    Setting(
        "RAG_DECISION_TIMEOUT",
        "decision_timeout",
        float,
        10.0,
        help="",
    ),
    Setting(
        "RAG_DECISION_THRESHOLD",
        "decision_threshold",
        float,
        0.13,
        help="",
    ),
    Setting(
        "RAG_DECISION_TOP_K",
        "decision_top_k",
        int,
        6,
        help="",
    ),
    Setting(
        "RAG_DECISION_RESERVOIR",
        "decision_reservoir",
        int,
        50,
        help="",
    ),
    # --- Autonomous research (Phase 1+: corpus discovery + gap analysis) ---
    Setting(
        "RAG_TARGETED_RETRY_V2",
        "targeted_retry_v2",
        bool,
        True,
        help="Arm-aware TargetPlan retry path (RAG-TR-001), active by default. "
        "Off = legacy string path (dormant: it silently echoes untargetable queries).",
    ),
    Setting(
        "RAG_VERIFIER_SHADOW",
        "verifier_shadow",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_ENABLED",
        "research_enabled",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_CORPUS_DISCOVERY",
        "research_corpus_discovery",
        bool,
        True,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_USE_PAYLOAD_INDEX",
        "research_use_payload_index",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_MAX_INGESTION_REQUESTS",
        "research_max_ingestion_requests",
        int,
        50,
        help="",
    ),
    Setting(
        "RAG_FEEDBACK_ENQUEUE_MAX_RATING",
        "feedback_enqueue_max_rating",
        int,
        2,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_SWEEP_ENABLED",
        "research_sweep_enabled",
        bool,
        True,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_SWEEP_MAX_ROWS",
        "research_sweep_max_rows",
        int,
        500,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_SESSION_MAX_TARGETS",
        "research_session_max_targets",
        int,
        5,
        help="",
    ),
    Setting(
        "RAG_RESEARCH_SESSION_MAX_LLM_CALLS",
        "research_session_max_llm_calls",
        int,
        5,
        help="",
    ),
    # --- Universal multihop (Part B) --------------------------------------
    Setting(
        "ENABLE_MULTIHOP",
        "multihop_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "MULTIHOP_CONFIDENCE_MIN",
        "multihop_confidence_min",
        str,
        "MEDIUM",
        help="",
    ),
    Setting(
        "MULTIHOP_MAX_FOLLOWUPS",
        "multihop_max_followups",
        int,
        1,
        help="",
    ),
    Setting(
        "MULTIHOP_MAX_REFS",
        "multihop_max_refs",
        int,
        2,
        help="",
    ),
    Setting(
        "RAG_AGENT_TASK_PARALLELISM",
        "task_parallelism",
        bool,
        True,
        help="",
    ),
    Setting(
        "ENABLE_LEGAL_IDENTITY",
        "legal_identity",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_CE_SECTION_PREFIX",
        "ce_section_prefix",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_MULTI_DOMAIN_ROUTER",
        "multi_domain_router",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    # --- Reranker ------------------------------------------------------------
    Setting(
        "RAG_ENSEMBLE_RERANK",
        "ensemble_rerank",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_RERANKER_MODEL",
        "reranker_model",
        str,
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        help="",
    ),
    Setting("RAG_RERANKER_ENDPOINT", "reranker_endpoint", str, "", help="Remote TEI /rerank URL; empty = local CE."),
    Setting("RAG_RERANKER_TOKEN", "reranker_token", str, "", help="Bearer token for the remote /rerank endpoint."),
    Setting(
        "RAG_RERANKER_MODE",
        "reranker_mode",
        str,
        "tei",
        help="",
    ),
    Setting("RAG_RERANKER_TIMEOUT", "reranker_timeout", float, 5.0, help="Per-request /rerank timeout in seconds."),
    Setting(
        "RAG_RERANKER_REMOTE_FALLBACK",
        "remote_rerank_fallback",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_ENSEMBLE_CE_HEAD",
        "ensemble_ce_head",
        int,
        30,
        help="",
    ),
    Setting(
        "RAG_ENSEMBLE_CE_WEIGHT",
        "ensemble_ce_weight",
        float,
        0.13,
        help="",
    ),
    # --- Remote dense embeddings ---------------------------------------------
    Setting(
        "RAG_EMBED_ENDPOINT",
        "embed_endpoint",
        str,
        "",
        help="",
    ),
    Setting("RAG_EMBED_TOKEN", "embed_token", str, "", help="Bearer token for the remote /embed endpoint."),
    Setting("RAG_EMBED_TIMEOUT", "embed_timeout", float, 5.0, help="Per-request /embed timeout in seconds."),
    Setting(
        "RAG_EMBED_REMOTE_FALLBACK",
        "embed_remote_fallback",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_EMBEDDING_MODEL",
        "embedding_model",
        str,
        "sentence-transformers/all-mpnet-base-v2",
        help="",
    ),
    # --- Qdrant / ingestion ---------------------------------------------------
    Setting("RAG_QDRANT_URL", "qdrant_url", str, "", help="Qdrant server URL."),
    Setting("RAG_QDRANT_API_KEY", "qdrant_api_key", str, "", help="Qdrant Cloud API key."),
    Setting("RAG_QDRANT_COLLECTION", "qdrant_collection", str, "fssai_legal_768", help="Default Qdrant collection."),
    # Multi-domain per-domain collection overrides (Phase 1 — de-FSSAI).
    # Consulted by app/rag/collections.collection_for_domain via config.get.
    Setting(
        "RAG_QDRANT_COLLECTION_ENV",
        "qdrant_collection_env",
        str,
        "env_legal_768",
        help="",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_COMMERCIAL",
        "qdrant_collection_commercial",
        str,
        "commercial_legal_768",
        help="",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_ANIMAL",
        "qdrant_collection_animal",
        str,
        "animal_legal_768",
        help="",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_WB_STATE",
        "qdrant_collection_wb_state",
        str,
        "wb_state_legal_768",
        help="",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_CRIMINAL",
        "qdrant_collection_criminal",
        str,
        "criminal_legal_768",
        help="",
    ),
    Setting("RAG_VECTOR_SIZE", "vector_size", int, 768, help="Embedding vector dimension."),
    Setting("RAG_SPARSE_MODEL", "sparse_model", str, "Qdrant/bm25", help="Fastembed sparse model for BM25 vectors."),
    Setting(
        "RAG_ENABLE_SPARSE",
        "enable_sparse",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FULL_ENRICHMENT",
        "full_enrichment",
        bool,
        False,
        help="",
    ),
    # --- Sync redundancy (Priority 7) -----------------------------------------
    Setting(
        "ENABLE_AIRTABLE_SYNC",
        "enable_airtable_sync",
        bool,
        True,
        help="",
    ),
    Setting(
        "ENABLE_EXCEL_SYNC",
        "enable_excel_sync",
        bool,
        False,
        help="",
    ),
    Setting(
        "BACKUP_FULL_ARCHIVE_ENABLED",
        "full_archive_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "BACKUP_ARCHIVE_RETENTION",
        "archive_retention",
        int,
        30,
        help="",
    ),
    Setting(
        "AUTO_RESTORE_ON_EMPTY_DB",
        "auto_restore_on_empty_db",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_BACKUP_SCHEDULE",
        "backup_schedule_enabled",
        bool,
        False,
        help="",
    ),
    Setting(
        "ENABLE_SNAPSHOT_SCHEDULE",
        "snapshot_schedule_enabled",
        bool,
        True,
        help="",
    ),
    Setting(
        "RAG_ENABLE_LOG_CLEANUP_SCHEDULE",
        "rag_log_cleanup_schedule_enabled",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_ENABLE_INGESTION_SCHEDULE",
        "enable_ingestion_schedule",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_INGESTION_CRON",
        "ingestion_cron",
        str,
        "0 3 * * *",
        help="",
    ),
    # --- Generation -------------------------------------------------------------
    Setting(
        "RAG_USE_STUB_LLM",
        "use_stub_llm",
        bool,
        False,
        help="",
    ),
    # --- Knowledge Graph -------------------------------------------------------
    Setting(
        "RAG_KG_EXPANSION",
        "kg_expansion",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_KG_FUSION",
        "kg_fusion",
        bool,
        False,
        help="",
    ),
    Setting(
        "RAG_KG_MAX_PROVISIONS",
        "kg_max_provisions",
        int,
        5,
        help="",
    ),
    Setting(
        "RAG_RETRIEVAL_CACHE_TTL_SECONDS",
        "retrieval_cache_ttl_seconds",
        int,
        90,
        help="",
    ),
    Setting(
        "RAG_LOG_RETENTION_DAYS",
        "rag_log_retention_days",
        int,
        90,
        help="",
    ),
    Setting(
        "RAG_KG_FAILURE_THRESHOLD",
        "kg_failure_threshold",
        int,
        5,
        help="",
    ),
    Setting(
        "RAG_KG_TIMEOUT_SECONDS",
        "kg_timeout_seconds",
        int,
        120,
        help="",
    ),
    # --- Context limits (ContextBuilder) ----------------------------------------
    Setting(
        "RAG_CONTEXT_MAX_CHUNKS",
        "context_max_chunks",
        int,
        50,
        help="",
    ),
    Setting(
        "RAG_CONTEXT_MAX_CHARS",
        "context_max_chars",
        int,
        40000,
        help="",
    ),
    Setting(
        "RAG_CONTEXT_DROP_INDEX_CHUNKS",
        "context_drop_index_chunks",
        bool,
        True,
        help="",
    ),
    Setting(
        "RAG_CONTEXT_DROP_AMENDMENT_PROSE",
        "context_drop_amendment_prose",
        bool,
        True,
        help="",
    ),
    # --- Runtime / plugins ------------------------------------------------------
    Setting("RAG_TORCH_THREADS", "torch_threads", int, 4, help="Torch intra/inter-op thread cap for RAG inference."),
    Setting("OCR_PROVIDER", "ocr_provider", str, "easyocr", help="Active OCR plugin."),
    Setting(
        "OCR_LANGUAGES",
        "ocr_languages",
        str,
        "english,hindi",
        help="",
    ),
    Setting(
        "OCR_USE_GPU",
        "ocr_use_gpu",
        bool,
        False,
        help="",
    ),
    Setting("AI_PROVIDER", "ai_provider", str, "openrouter", help="Active AI plugin."),
    Setting("RULES_PROVIDER", "rules_provider", str, "fssai_default", help="Active rules plugin."),
    Setting("PDF_PROVIDER", "pdf_provider", str, "weasyprint", help="Active PDF plugin."),
    # Supabase sync is now ALWAYS enabled. The flag is retained for
    # backward compatibility (always reads True) but is no longer used
    # to gate the service.
    Setting(
        "ENABLE_SUPABASE_SYNC",
        "supabase_sync_enabled",
        bool,
        True,
        help="",
        opt_in=False,
    ),
    Setting(
        "SUPABASE_URL",
        "supabase_url",
        str,
        "",
        help="",
    ),
    Setting(
        "SUPABASE_API_KEY",
        "supabase_api_key",
        str,
        "",
        help="",
    ),
    Setting(
        "SUPABASE_SYNC_INTERVAL",
        "supabase_sync_interval",
        int,
        300,
        help="",
    ),
    Setting(
        "SUPABASE_DB_URL",
        "supabase_db_url",
        str,
        "",
        help="",
    ),
    Setting(
        "PREMISES_SEARCH_LOCALITY_ENABLED",
        "premises_search_locality_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "DIARY_SCHEDULE_AI_ENABLED",
        "diary_schedule_ai_enabled",
        bool,
        False,
        opt_in=True,
        help="",
    ),
    # --- Notepad --------------------------------------------------------------
    Setting(
        "NOTEPAD_AI_ENABLED",
        "notepad_ai_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    # --- Case file adjudication UI -------------------------------------------
    Setting(
        "CASE_FILE_UNSAFE_OPTION_ENABLED",
        "case_file_unsafe_option_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    # --- FSO strategic advisory (game-theory + Talebian, ADR-0003) ------------
    Setting(
        "FSO_ADVISOR_ENABLED",
        "fso_advisor_enabled",
        bool,
        False,
        help="",
    ),
    # --- FBO compliance auditor (FBO_AUDITOR_AGENT_BLUEPRINT) ------------------
    Setting(
        "AUDITOR_AI_ENABLED",
        "auditor_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    # --- Probabilistic photo-evidence assessment (ADR-0011) -------------------
    Setting(
        "PHOTO_SCORING_ENABLED",
        "photo_scoring_enabled",
        bool,
        False,
        help="",
    ),
    Setting(
        "PHOTO_SCORING_MODEL",
        "photo_scoring_model",
        str,
        "inception/mercury-decide:free",
        help="",
    ),
    Setting(
        "PHOTO_SCORING_ENDPOINT",
        "photo_scoring_endpoint",
        str,
        "https://openrouter.ai/api/v1/systemone",
        help="",
    ),
    Setting(
        "PHOTO_SCORING_THRESHOLD",
        "photo_scoring_threshold",
        float,
        0.70,
        help="",
    ),
    Setting(
        "PHOTO_SCORING_TIMEOUT",
        "photo_scoring_timeout",
        float,
        60.0,
        help="",
    ),
    Setting(
        "PHOTO_SCORING_MAX_BYTES",
        "photo_scoring_max_bytes",
        int,
        # Matches app/utils/storage.MAX_FILE_SIZE, so a photo that stored
        # successfully is never permanently unscoreable. Keep the two in step.
        5 * 1024 * 1024,
        help="",
    ),
    # --- Entity–provision-aware food retrieval (2026-09-28) --------------------
    Setting(
        "RAG_FOOD_INTENT_ENABLED",
        "food_intent_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FOOD_LEGAL_RERANK",
        "food_legal_rerank",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FOOD_PARENT_RECONSTRUCT",
        "food_parent_reconstruct",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FOOD_VALIDATE",
        "food_validate",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FOOD_ANSWER_MODE",
        "food_answer_mode",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "RAG_FOOD_FALLBACK_ROUNDS",
        "food_fallback_rounds",
        int,
        2,
        help="",
    ),
    # --- Statutory provision extractor (ADR-0009 Tier 1 rules mode) ----------
    Setting(
        "PROVISION_EXTRACTOR_ENABLED",
        "provision_extractor_enabled",
        bool,
        True,
        opt_in=False,
        help="",
    ),
    Setting(
        "PROVISION_EXTRACTOR_MODE",
        "provision_extractor_mode",
        str,
        "rules",
        help="",
    ),
    Setting(
        "PROVISION_EXTRACTOR_MODEL_PATH",
        "provision_extractor_model_path",
        str,
        "models/provision_boundaries.joblib",
        help="",
    ),
    Setting(
        "PROVISION_EXTRACTOR_MIN_CONFIDENCE",
        "provision_extractor_min_confidence",
        float,
        0.70,
        help="",
    ),
    Setting(
        "PROVISION_EXTRACTOR_ML_THRESHOLD",
        "provision_extractor_ml_threshold",
        float,
        0.25,
        help="",
    ),
    Setting(
        "PROVISION_EXTRACTOR_REVIEW_THRESHOLD",
        "provision_extractor_review_threshold",
        float,
        0.30,
        help="",
    ),
    # --- Progressive Web App (issue #70) --------------------------------------
    Setting(
        "ENABLE_PWA",
        "enable_pwa",
        bool,
        False,
        opt_in=True,
        help="",
    ),
    # --- Observability (issues #71-#75) --------------------------------------
    # Error tracking. A blank DSN means Sentry is never initialised: with no DSN
    # there is no transport, so there is no network egress (the property the
    # test suite's no-network contract needs). Never pass dsn=None - the SDK
    # falls back to $SENTRY_DSN. See docs/OBSERVABILITY_SENTRY_PROMETHEUS_SPEC.md.
    Setting(
        "SENTRY_DSN",
        "sentry_dsn",
        str,
        "",
        help="",
    ),
    Setting(
        "SENTRY_ENVIRONMENT",
        "sentry_environment",
        str,
        "",
        help="",
    ),
    Setting(
        "SENTRY_TRACES_SAMPLE_RATE",
        "sentry_traces_sample_rate",
        float,
        0.0,
        help="",
    ),
    # Prometheus scraping. GET /metrics is bearer-token guarded and fail-closed:
    # enabled with a blank METRICS_TOKEN returns 503, never 200.
    # PROMETHEUS_MULTIPROC_DIR is deliberately NOT declared here - the client
    # reads it at import time, before this table exists.
    Setting(
        "METRICS_ENABLED",
        "metrics_enabled",
        bool,
        False,
        opt_in=True,
        help="",
    ),
    Setting(
        "METRICS_TOKEN",
        "metrics_token",
        str,
        "",
        help="",
    ),
)
_BY_ATTR: dict[str, Setting] = {s.attr: s for s in _TABLE}
_BY_KEY: dict[str, Setting] = {s.key: s for s in _TABLE}


def _parse(setting: Setting, value: Any) -> Any:
    """Coerce *value* to the setting's declared type.

    ``None`` means "not configured anywhere" → declared default. Real bools
    pass through; strings are parsed per the declared convention (this fixes
    the historical ``bool("false") is True`` trap). Numeric garbage falls
    back to the declared default instead of raising.
    """
    if value is None:
        return setting.default
    if setting.type is bool:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        return s == "true" if setting.opt_in else s != "false"
    if setting.type is str:
        return str(value)
    try:
        return setting.type(value)
    except (TypeError, ValueError):
        return setting.default


def _resolve_raw(key: str) -> Any:
    """Pattern A raw lookup: Flask config inside an app context, else env.

    Returns ``None`` when nothing is configured — callers parse it into the
    declared default via :func:`_parse`.
    """
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app.config.get(key)
    except Exception:  # pragma: no cover - Flask import edge cases
        pass
    return os.environ.get(key)


def _resolve_setting(setting: Setting) -> Any:
    """Pattern A resolution: config (in-context) → env (out-of-context) → default."""
    return _parse(setting, _resolve_raw(setting.key))


class _Cfg:
    """Named access to the declaration table: ``cfg.kg_fusion`` etc."""

    def __getattr__(self, attr: str) -> Any:
        try:
            setting = _BY_ATTR[attr]
        except KeyError:
            raise AttributeError(f"cfg has no setting {attr!r}. Declared settings: {sorted(_BY_ATTR)}") from None
        return _resolve_setting(setting)

    # -- Generic accessors (dynamic keys, e.g. per-category plugin keys) ----
    def get_bool(self, key: str, default: bool = False, *, opt_in: bool = True) -> bool:
        """Resolve a boolean flag by key (same rule as named accessors)."""
        return bool(
            _parse(
                Setting(key, key, bool, default, opt_in=opt_in),
                _resolve_raw(key),
            ),
        )

    def get_str(self, key: str, default: str = "") -> str:
        """Resolve a string setting by key."""
        return str(_parse(Setting(key, key, str, default), _resolve_raw(key)))

    # -- Introspection -------------------------------------------------------
    def table(self) -> tuple[Setting, ...]:
        """Return the declaration table (documentation / meta-tests)."""
        return _TABLE

    def describe(self) -> dict[str, dict[str, Any]]:
        """Return ``{key: {attr, type, default, opt_in, help}}`` for docs."""
        return {
            s.key: {"attr": s.attr, "type": s.type.__name__, "default": s.default, "opt_in": s.opt_in, "help": s.help}
            for s in _TABLE
        }


cfg = _Cfg()


def seed_config_from_env(app: Any) -> int:
    """Seed *app*.config for every declared setting not already present.

    Called once from ``create_app()``. Each declared key is set to the
    env-parsed value when the env var is present, otherwise to its **declared
    default** — so soft readers (``current_app.config.get(...)``) inside an
    app context see exactly what an out-of-context caller resolves under
    Pattern A, and "what the app is configured with" stays fully inspectable
    in one place. Keys already in config (hand-seeded earlier in the factory)
    are left untouched. Returns the number of keys seeded.
    """
    seeded = 0
    for setting in _TABLE:
        if setting.key in app.config:
            continue
        app.config[setting.key] = _parse(setting, os.environ.get(setting.key))
        seeded += 1
    return seeded
