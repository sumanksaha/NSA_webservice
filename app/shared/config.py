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
        help="Master switch for the RAG module (503/404 when off).",
    ),
    Setting(
        "RAG_USE_AGENT_PIPELINE",
        "use_agent_pipeline",
        bool,
        False,
        help="LangGraph agent pipeline on POST /api/rag/query/agent (M3).",
    ),
    Setting("RAG_AGENT_HITL", "agent_hitl", bool, False, help="M5 human-in-the-loop review interrupt before finalize."),
    Setting(
        "RAG_AGENT_CHECKPOINTER",
        "agent_checkpointer",
        str,
        "memory",
        help="M5 checkpointer: 'memory' (default) or 'postgres'.",
    ),
    Setting(
        "RAG_HALLUCINATION_DETECTOR",
        "hallucination_detector",
        bool,
        True,
        opt_in=False,
        help="Phase 3 claim-level HallucinationDetector in run_generation_pipeline.",
    ),
    # --- Retrieval pipeline flags -------------------------------------------
    Setting(
        "RAG_RETRIEVAL_CACHE",
        "retrieval_cache",
        bool,
        False,
        help="Memoize deterministic retrieval results (TTL+LRU, §12.1).",
    ),
    Setting(
        "RAG_QDRANT_BM25",
        "qdrant_bm25",
        bool,
        False,
        help="Qdrant-side BM25 sparse inference (Qdrant/bm25) at query time.",
    ),
    Setting(
        "RAG_LEGAL_QUERY_TYPING",
        "legal_query_typing",
        bool,
        True,
        opt_in=False,
        help="Rule-based legal query-type classifier feeding reranker weights.",
    ),
    Setting(
        "RAG_IDENTIFIER_ROUTE",
        "identifier_route",
        bool,
        True,
        opt_in=False,
        help="Lexical '{Act} section {N}' parallel retrieval arm (V5-validated).",
    ),
    Setting(
        "ENABLE_EVIDENCE_SELECTOR",
        "evidence_selector",
        bool,
        False,
        help="Evidence-set selection over top-K (opt-in A/B lever).",
    ),
    Setting(
        "ENABLE_REFERENCE_EXPANSION",
        "reference_expansion",
        bool,
        False,
        help="Reference-graph candidate expansion (default off per spec).",
    ),
    Setting(
        "ENABLE_LEGAL_IDENTITY",
        "legal_identity",
        bool,
        True,
        opt_in=False,
        help="Canonical legal-identity parsing of retrieved chunks.",
    ),
    Setting(
        "RAG_CE_SECTION_PREFIX",
        "ce_section_prefix",
        bool,
        False,
        help="Prefix CE passages with §-identity before scoring (CV2 P1).",
    ),
    # --- Reranker ------------------------------------------------------------
    Setting(
        "RAG_ENSEMBLE_RERANK",
        "ensemble_rerank",
        bool,
        True,
        opt_in=False,
        help="sec_act + CE ensemble reranker (default on; false = plain Reranker).",
    ),
    Setting(
        "RAG_RERANKER_MODEL",
        "reranker_model",
        str,
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
        help="Cross-encoder model (fine-tuned legal CE is a drop-in).",
    ),
    Setting("RAG_RERANKER_ENDPOINT", "reranker_endpoint", str, "", help="Remote TEI /rerank URL; empty = local CE."),
    Setting("RAG_RERANKER_TOKEN", "reranker_token", str, "", help="Bearer token for the remote /rerank endpoint."),
    Setting(
        "RAG_RERANKER_MODE",
        "reranker_mode",
        str,
        "tei",
        help="Remote CE backend: 'tei' (default). Never 'serverless' (decommissioned).",
    ),
    Setting("RAG_RERANKER_TIMEOUT", "reranker_timeout", float, 5.0, help="Per-request /rerank timeout in seconds."),
    Setting(
        "RAG_RERANKER_REMOTE_FALLBACK",
        "remote_rerank_fallback",
        bool,
        True,
        opt_in=False,
        help="Lazy local-CE fallback when the remote endpoint fails.",
    ),
    Setting(
        "RAG_ENSEMBLE_CE_HEAD",
        "ensemble_ce_head",
        int,
        30,
        help="Post-sec_act head size the CE scores (validated h=30).",
    ),
    Setting(
        "RAG_ENSEMBLE_CE_WEIGHT", "ensemble_ce_weight", float, 0.5, help="Bonus weight for normalized CE head scores."
    ),
    # --- Remote dense embeddings ---------------------------------------------
    Setting(
        "RAG_EMBED_ENDPOINT",
        "embed_endpoint",
        str,
        "",
        help="Remote /embed URL (Modal); empty = local SentenceTransformer.",
    ),
    Setting("RAG_EMBED_TOKEN", "embed_token", str, "", help="Bearer token for the remote /embed endpoint."),
    Setting("RAG_EMBED_TIMEOUT", "embed_timeout", float, 5.0, help="Per-request /embed timeout in seconds."),
    Setting(
        "RAG_EMBED_REMOTE_FALLBACK",
        "embed_remote_fallback",
        bool,
        True,
        opt_in=False,
        help="Lazy local-embedder fallback when the remote endpoint fails.",
    ),
    Setting(
        "RAG_EMBEDDING_MODEL",
        "embedding_model",
        str,
        "sentence-transformers/all-mpnet-base-v2",
        help="Dense embedding model (768-dim).",
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
        help="Per-domain Qdrant collection override (environmental).",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_COMMERCIAL",
        "qdrant_collection_commercial",
        str,
        "commercial_legal_768",
        help="Per-domain Qdrant collection override (commercial).",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_ANIMAL",
        "qdrant_collection_animal",
        str,
        "animal_legal_768",
        help="Per-domain Qdrant collection override (animal husbandry).",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_WB_STATE",
        "qdrant_collection_wb_state",
        str,
        "wb_state_legal_768",
        help="Per-domain Qdrant collection override (WB state laws).",
    ),
    Setting(
        "RAG_QDRANT_COLLECTION_CRIMINAL",
        "qdrant_collection_criminal",
        str,
        "criminal_legal_768",
        help="Per-domain Qdrant collection override (criminal / BNS).",
    ),
    Setting("RAG_VECTOR_SIZE", "vector_size", int, 768, help="Embedding vector dimension."),
    Setting("RAG_SPARSE_MODEL", "sparse_model", str, "Qdrant/bm25", help="Fastembed sparse model for BM25 vectors."),
    Setting(
        "RAG_ENABLE_SPARSE",
        "enable_sparse",
        bool,
        True,
        opt_in=False,
        help="Upsert BM25 sparse vectors at ingestion (when collection declares them).",
    ),
    Setting(
        "RAG_FULL_ENRICHMENT",
        "full_enrichment",
        bool,
        False,
        help="Full Phase 2 enrichment adapter chain at ingestion.",
    ),
    # --- Sync redundancy (Priority 7) -----------------------------------------
    Setting(
        "ENABLE_AIRTABLE_SYNC",
        "enable_airtable_sync",
        bool,
        False,
        help="Airtable redundancy sync (dormant unless true).",
    ),
    Setting(
        "ENABLE_EXCEL_SYNC",
        "enable_excel_sync",
        bool,
        False,
        help="MS Excel Online sync (dormant until M365 credentials exist).",
    ),
    Setting(
        "BACKUP_FULL_ARCHIVE_ENABLED",
        "full_archive_enabled",
        bool,
        True,
        opt_in=False,
        help="Upload a full ZIP snapshot (DB dump + instance files) to R2 on every backup run.",
    ),
    Setting(
        "BACKUP_ARCHIVE_RETENTION",
        "archive_retention",
        int,
        30,
        help="Keep the newest N full-archive ZIPs in R2; older ones are pruned.",
    ),
    Setting(
        "AUTO_RESTORE_ON_EMPTY_DB",
        "auto_restore_on_empty_db",
        bool,
        False,
        help="At boot, replenish an empty database from R2 backups (full archive first, then CSV chain).",
    ),
    Setting(
        "ENABLE_BACKUP_SCHEDULE",
        "backup_schedule_enabled",
        bool,
        False,
        help="Register the daily QStash backup schedule at startup (ScheduledJobs).",
    ),
    Setting(
        "RAG_ENABLE_INGESTION_SCHEDULE",
        "enable_ingestion_schedule",
        bool,
        False,
        help="Register the daily QStash corpus-ingestion schedule at startup (ScheduledJobs).",
    ),
    Setting(
        "RAG_INGESTION_CRON",
        "ingestion_cron",
        str,
        "0 3 * * *",
        help="Cron expression for the corpus-ingestion schedule.",
    ),
    # --- Generation -------------------------------------------------------------
    Setting(
        "RAG_USE_STUB_LLM",
        "use_stub_llm",
        bool,
        False,
        help="Stub LLM mode for grounded generation (tests / offline runs).",
    ),
    # --- Knowledge Graph -------------------------------------------------------
    Setting(
        "RAG_KG_EXPANSION",
        "kg_expansion",
        bool,
        False,
        help="Expand retrieved chunk IDs through the Neo4j KG (mutually exclusive with fusion).",
    ),
    Setting(
        "RAG_KG_FUSION",
        "kg_fusion",
        bool,
        False,
        help="RRF-fuse KG provisions into retrieved context (mutually exclusive with expansion).",
    ),
    Setting(
        "RAG_KG_MAX_PROVISIONS", "kg_max_provisions", int, 5, help="Max KG provisions injected into the LLM context."
    ),
    # --- Runtime / plugins ------------------------------------------------------
    Setting("RAG_TORCH_THREADS", "torch_threads", int, 4, help="Torch intra/inter-op thread cap for RAG inference."),
    Setting("OCR_PROVIDER", "ocr_provider", str, "easyocr", help="Active OCR plugin."),
    Setting(
        "OCR_LANGUAGES",
        "ocr_languages",
        str,
        "english,hindi",
        help="Comma-separated languages for the active OCR provider (adapter splits on ',').",
    ),
    Setting(
        "OCR_USE_GPU",
        "ocr_use_gpu",
        bool,
        False,
        help="GPU for OCR inference (keep false on CPU-only hosts, e.g. Render free tier).",
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
        help="[DEPRECATED] Supabase sync is always enabled and synchronous.",
        opt_in=False,
    ),
    Setting(
        "SUPABASE_URL",
        "supabase_url",
        str,
        "",
        help="Supabase project URL for the cloud-sync bridge.",
    ),
    Setting(
        "SUPABASE_API_KEY",
        "supabase_api_key",
        str,
        "",
        help="Supabase anon/service-role key for the cloud-sync bridge.",
    ),
    Setting(
        "SUPABASE_SYNC_INTERVAL",
        "supabase_sync_interval",
        int,
        300,
        help="Polling interval (seconds) for automatic sync operations.",
    ),
    # --- Notepad --------------------------------------------------------------
    Setting(
        "NOTEPAD_AI_ENABLED",
        "notepad_ai_enabled",
        bool,
        True,
        opt_in=False,
        help="Kill switch for Notepad AI evaluation (LLM spend). 503 when off.",
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
            )
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
