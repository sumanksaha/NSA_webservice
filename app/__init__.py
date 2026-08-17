import os
import secrets
import threading
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, redirect, request, url_for
from flask_login import current_user
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import csrf, db, login_manager, talisman

_fso_sync_lock = threading.Lock()

# Module-level Celery instance — populated after app factory runs
celery = None


class App(Flask):
    """Flask app subclass with a typed ``celery`` attribute."""

    celery: Any = None


def _load_or_create_production_secret_key(app: Flask) -> str:
    """Return a stable production SECRET_KEY, generating + persisting one if needed.

    Render's ``generateValue: true`` only mints a value when the env var is
    FIRST created on the service, so services that predate the setting (or
    were created from the dashboard) can boot with no SECRET_KEY. Instead of
    crashing the deploy, generate a strong key once and persist it so
    sessions stay valid across restarts and redeploys.

    Persistence order (first success wins):
      1. ``app_secrets`` key/value table in the primary DB (survives
         redeploys on Render's persistent Postgres).
      2. ``<instance_path>/.secret_key`` file (survives restarts).
      3. Ephemeral in-memory key — sessions reset on restart, but the app
         still boots (never blocks a deploy).

    An explicit ``SECRET_KEY`` env var (dashboard-managed or a
    generateValue-minted value) always takes precedence and bypasses this.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Normalize postgres:// -> postgresql:// (same as create_app does)
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(database_url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS app_secrets (name VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)",
                    ),
                )
                row = conn.execute(
                    text("SELECT value FROM app_secrets WHERE name = 'secret_key'"),
                ).fetchone()
                if row:
                    app.logger.info("SECRET_KEY auto-provisioned (DB — reuse)")
                    return str(row[0])
                new_key = secrets.token_hex(32)
                conn.execute(
                    text(
                        "INSERT INTO app_secrets (name, value) VALUES ('secret_key', :v) ON CONFLICT(name) DO NOTHING",
                    ),
                    {"v": new_key},
                )
                row = conn.execute(
                    text("SELECT value FROM app_secrets WHERE name = 'secret_key'"),
                ).fetchone()
                app.logger.info("SECRET_KEY auto-provisioned (DB — new)")
                return str(row[0]) if row else new_key
        except Exception as exc:
            app.logger.warning("SECRET_KEY DB persistence unavailable: %s", exc)

    # Instance-folder file fallback (survives restarts on the same instance)
    try:
        key_file = Path(app.instance_path) / ".secret_key"
        if key_file.exists():
            stored = key_file.read_text().strip()
            if stored:
                app.logger.info("SECRET_KEY auto-provisioned (file — reuse)")
                return stored
        new_key = secrets.token_hex(32)
        key_file.write_text(new_key)
        app.logger.info("SECRET_KEY auto-provisioned (file — new)")
        return new_key
    except OSError as exc:
        app.logger.warning("SECRET_KEY file fallback unavailable: %s", exc)

    # Ephemeral last resort — never block a deploy; sessions reset on restart
    app.logger.warning(
        "SECRET_KEY not set and no persistence available — using ephemeral key; "
        "sessions will reset on restart. Provision SECRET_KEY in Render "
        "(render.yaml generateValue: true for new services, or set it in the "
        "dashboard for existing services).",
    )
    return secrets.token_hex(32)


def create_app():
    app = App(__name__)

    # Load environment variables from .env file before any config
    load_dotenv()

    # ------------------------------------------------------------------
    # Production detection — shared by the SECRET_KEY guard below and the
    # TLS/security-header config further down. Render sets RENDER on every
    # service; APP_ENV / FLASK_ENV cover other hosts. Hoisted here so the
    # SECRET_KEY guard can reuse it instead of only checking RENDER.
    # ------------------------------------------------------------------
    is_production = (
        bool(os.environ.get("RENDER"))
        or os.environ.get("APP_ENV", "").lower() in ("production", "prod")
        or os.environ.get("FLASK_ENV", "").lower() == "production"
    )

    # Ensure instance folder exists before the SECRET_KEY guard — the
    # production fallback may write a key file there on first boot.
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        app.logger.warning(f"Could not create instance directory: {app.instance_path}")

    # ------------------------------------------------------------------
    # Mandatory: SECRET_KEY — required for session signing, flash messages,
    #             CSRF tokens, and any cryptographic signing in Flask.
    # ------------------------------------------------------------------
    # In production this MUST be a long, random value.  Generate one with:
    #     python -c "import secrets; print(secrets.token_hex(32))"
    # Render: provision it as a managed value (render.yaml generateValue: true)
    # or via the Render dashboard. Never commit a real key to the repo.
    # ------------------------------------------------------------------
    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        if is_production:
            # Auto-provision so the deploy can never be blocked (Render only
            # mints generateValue secrets when the env var is first created).
            secret_key = _load_or_create_production_secret_key(app)
        else:
            # In local development, use a fallback so the app can start without
            # requiring every developer to create a .env file immediately.
            # Gated behind is_production so production never silently falls
            # back to a fresh random key (that would rotate every session).
            secret_key = secrets.token_hex(32)
            app.logger.warning(
                "SECRET_KEY not set — using insecure local fallback. "
                "Set SECRET_KEY in your .env file for local development.",
            )
    app.config["SECRET_KEY"] = secret_key

    # Database configuration - PostgreSQL primary, SQLite fallback
    db_path = Path(app.instance_path) / "app.db"
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        # Normalize postgres:// to postgresql:// for SQLAlchemy compatibility
        # (Render still issues the old postgres:// scheme)
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        # Validate URL has a scheme (basic check for malformed URLs)
        if not any(
            database_url.startswith(proto) for proto in ["postgresql://", "sqlite://", "mysql://", "mariadb://"]
        ):
            app.logger.warning(f"DATABASE_URL malformed: '{database_url}' - falling back to SQLite")
            database_url = f"sqlite:///{db_path}"
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
        app.logger.warning("DATABASE_URL not set - falling back to SQLite")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Redis configuration (can be set via environment variable)
    app.config["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Google Sheets configuration (can be set via environment variables)
    app.config["SPREADSHEET_ID"] = os.environ.get("SPREADSHEET_ID")
    app.config["GOOGLE_CREDENTIALS_JSON"] = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    # Priority 7 — Multi-Target Sheets Redundancy configuration
    app.config["AIRTABLE_API_KEY"] = os.environ.get("AIRTABLE_API_KEY")
    app.config["AIRTABLE_BASE_ID"] = os.environ.get("AIRTABLE_BASE_ID")
    app.config["ENABLE_AIRTABLE_SYNC"] = os.environ.get("ENABLE_AIRTABLE_SYNC", "false").lower() == "true"

    # Microsoft Excel Online configuration (Priority 7)
    app.config["MS_TENANT_ID"] = os.environ.get("MS_TENANT_ID")
    app.config["MS_CLIENT_ID"] = os.environ.get("MS_CLIENT_ID")
    app.config["MS_CLIENT_SECRET"] = os.environ.get("MS_CLIENT_SECRET")
    app.config["MS_DRIVE_ID"] = os.environ.get("MS_DRIVE_ID")
    app.config["MS_SPREADSHEET_ID"] = os.environ.get("MS_SPREADSHEET_ID")
    app.config["ENABLE_EXCEL_SYNC"] = os.environ.get("ENABLE_EXCEL_SYNC", "false").lower() == "true"

    # ------------------------------------------------------------------
    # Phase 11: AI Assistant configuration
    # ------------------------------------------------------------------
    app.config["AI_ASSISTANT_PROVIDER"] = os.environ.get("AI_ASSISTANT_PROVIDER", "")
    app.config["AI_ASSISTANT_API_KEY"] = os.environ.get("AI_ASSISTANT_API_KEY", "")
    app.config["AI_ASSISTANT_BASE_URL"] = os.environ.get("AI_ASSISTANT_BASE_URL")
    app.config["AI_ASSISTANT_MODEL"] = os.environ.get("AI_ASSISTANT_MODEL")

    # ------------------------------------------------------------------
    # Phase B: RAG (Retrieval / Generation / Evaluation) configuration
    # ------------------------------------------------------------------
    app.config["RAG_ENABLED"] = os.environ.get("RAG_ENABLED", "true").lower() == "true"
    app.config["RAG_QDRANT_URL"] = os.environ.get("RAG_QDRANT_URL", "")
    app.config["RAG_QDRANT_API_KEY"] = os.environ.get("RAG_QDRANT_API_KEY", "")
    app.config["RAG_QDRANT_COLLECTION"] = os.environ.get("RAG_QDRANT_COLLECTION", "fssai_legal_768")
    # Multi-domain RAG collections (Phase 1 — de-FSSAI, 2026-08-10): per-domain
    # collection overrides consulted by app/rag/collections.collection_for_domain.
    # Defaults mirror the multi-domain manifest; the FSSAI corpus stays on
    # RAG_QDRANT_COLLECTION.
    app.config["RAG_QDRANT_COLLECTION_ENV"] = os.environ.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768")
    app.config["RAG_QDRANT_COLLECTION_COMMERCIAL"] = os.environ.get(
        "RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"
    )
    app.config["RAG_QDRANT_COLLECTION_ANIMAL"] = os.environ.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768")
    app.config["RAG_QDRANT_COLLECTION_WB_STATE"] = os.environ.get(
        "RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"
    )
    app.config["RAG_QDRANT_COLLECTION_CRIMINAL"] = os.environ.get(
        "RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"
    )
    app.config["RAG_VECTOR_SIZE"] = int(os.environ.get("RAG_VECTOR_SIZE", "768"))
    app.config["RAG_EMBEDDING_MODEL"] = os.environ.get("RAG_EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")
    app.config["RAG_RERANKER_MODEL"] = os.environ.get("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    # Torch thread cap for RAG inference (embedding + CE reranking).  On
    # laptops the torch default (one thread per core) pegs every core during
    # a single query; 4 threads is the measured sweet spot (i5-1135G7 class)
    # and leaves the machine usable while a query runs.
    try:
        app.config["RAG_TORCH_THREADS"] = int(os.environ.get("RAG_TORCH_THREADS", "4"))
    except ValueError:
        app.config["RAG_TORCH_THREADS"] = 4
    # BM25 sparse (hybrid) retrieval — local fastembed "Qdrant/bm25" vectors.
    # On: new collections are created with a named sparse vector and ingestion
    #     embeds it; existing dense-only collections keep working (dense-only)
    #     until recreated.
    app.config["RAG_ENABLE_SPARSE"] = os.environ.get("RAG_ENABLE_SPARSE", "true").lower() == "true"
    app.config["RAG_SPARSE_MODEL"] = os.environ.get("RAG_SPARSE_MODEL", "Qdrant/bm25")
    # When true, the production ingestion pipeline (make_ingestion_pipeline)
    # wires the full Phase 2 adapter chain (metadata / citation / crossref /
    # quality) in addition to the always-on DocumentClassifier.
    app.config["RAG_FULL_ENRICHMENT"] = os.environ.get("RAG_FULL_ENRICHMENT", "false").lower() == "true"
    # LLM model for grounded RAG generation (OpenRouter free tier).
    # Overrides the GroundedLLMClient default when set.
    app.config["RAG_LLM_MODEL"] = os.environ.get("RAG_LLM_MODEL")
    # KG graph expansion (Option F — 2026-08-11): when true, the generation
    # pipeline expands retrieved chunk IDs through the Neo4j legal KG into
    # structured legal context (provisions, domains, temporal status).
    # Best-effort — a missing/unreachable Neo4j degrades to no expansion.
    app.config["RAG_KG_EXPANSION"] = os.environ.get("RAG_KG_EXPANSION", "false").lower() == "true"
    # Legal-identifier route (2026-08-13): build a lexical "{Act} section {N}"
    # query from identifiers detected in the question and run it through the
    # sparse retriever as a parallel additive arm fused with dense+sparse RRF.
    # Validated by the V5/V5.5 evaluation arc (+13.3pp candidate-pool ceiling;
    # pool 100% after the section-stamp backfill).  Default true — pure lexical
    # retrieval, no external dependency.  Set RAG_IDENTIFIER_ROUTE=false to
    # disable.
    app.config["RAG_IDENTIFIER_ROUTE"] = os.environ.get("RAG_IDENTIFIER_ROUTE", "true").lower() != "false"
    # sec_act + cross-encoder ensemble reranker (2026-08-14, validated by the
    # CE_RERANK_REVIEW): when true, retrieval ranking uses the deterministic
    # sec_act legal features as primary (strongest single reranker measured:
    # R@10 0.474 on the P1 head) with the cross-encoder scored only on the
    # post-sec_act top-K head as a complementary second opinion (union any-hit
    # R@10 62.0% vs 56.7%).  The deterministic half needs no external
    # dependency; the CE half degrades to features-only when unavailable.
    # Set RAG_ENSEMBLE_RERANK=false for the plain (cross-encoder-or-BM25)
    # Reranker.
    app.config["RAG_ENSEMBLE_RERANK"] = os.environ.get("RAG_ENSEMBLE_RERANK", "true").lower() != "false"
    # How many post-sec_act chunks the cross-encoder scores (latency bound).
    # Validated by the CE weight/head sweep (2026-08-09): h=30 yields the best
    # R@10=0.4267 (+5.1pp over h=20's 0.3761 at w=0.5); weight tuning within
    # h=30 plateaus at 0.4267 for w=0.5-0.8, so weight stays at 0.5.
    try:
        app.config["RAG_ENSEMBLE_CE_HEAD"] = int(os.environ.get("RAG_ENSEMBLE_CE_HEAD", "30"))
    except ValueError:
        app.config["RAG_ENSEMBLE_CE_HEAD"] = 30
    # Bonus weight applied to the normalized CE scores on the head.
    try:
        app.config["RAG_ENSEMBLE_CE_WEIGHT"] = float(os.environ.get("RAG_ENSEMBLE_CE_WEIGHT", "0.5"))
    except ValueError:
        app.config["RAG_ENSEMBLE_CE_WEIGHT"] = 0.5
    # Remote cross-encoder hosting (docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md
    # Part B): when RAG_RERANKER_ENDPOINT is set, the ensemble reranker's CE
    # head is scored via a TEI (text-embeddings-inference) /rerank HTTP endpoint
    # (HF Docker Space / Inference Endpoint) instead of a local torch model.
    # The endpoint is a drop-in for the local encoder — the sec_act feature
    # half and all reranker scoring logic are unchanged.  Empty endpoint ⇒
    # local CE as before.
    app.config["RAG_RERANKER_ENDPOINT"] = os.environ.get("RAG_RERANKER_ENDPOINT", "")
    app.config["RAG_RERANKER_TOKEN"] = os.environ.get("RAG_RERANKER_TOKEN", "")
    try:
        app.config["RAG_RERANKER_TIMEOUT"] = float(os.environ.get("RAG_RERANKER_TIMEOUT", "5"))
    except ValueError:
        app.config["RAG_RERANKER_TIMEOUT"] = 5.0
    # Remote CE backend: "tei" (default — TEI /rerank, one batched POST per
    # query) or "serverless" (HF Serverless Inference API — per-pair [SEP]
    # requests, free tier, no Docker Space / Endpoint needed).  Ignored when
    # RAG_RERANKER_ENDPOINT is empty.
    app.config["RAG_RERANKER_MODE"] = os.environ.get("RAG_RERANKER_MODE", "tei")
    # Fall back to the local CE (lazy — built only on first remote failure)
    # when the remote endpoint errors.  False ⇒ remote failure degrades
    # straight to sec_act features-only.
    app.config["RAG_RERANKER_REMOTE_FALLBACK"] = (
        os.environ.get("RAG_RERANKER_REMOTE_FALLBACK", "true").lower() != "false"
    )
    # Remote dense-embedding hosting (Modal / TEI-style /embed — see
    # app/rag/retrieval/remote_embedder.py): when RAG_EMBED_ENDPOINT is set,
    # DenseRetriever embeds queries over HTTP instead of loading
    # all-mpnet-base-v2 + torch locally (Render free tier cannot hold them).
    # The endpoint returns {"vectors": [...]} in the same embedding space as
    # the local model, so retrieval logic is unchanged.  Empty ⇒ local encoder
    # as before.  Ingestion-side embedding stays local (one-off scripts).
    app.config["RAG_EMBED_ENDPOINT"] = os.environ.get("RAG_EMBED_ENDPOINT", "")
    app.config["RAG_EMBED_TOKEN"] = os.environ.get("RAG_EMBED_TOKEN", "")
    try:
        app.config["RAG_EMBED_TIMEOUT"] = float(os.environ.get("RAG_EMBED_TIMEOUT", "5"))
    except ValueError:
        app.config["RAG_EMBED_TIMEOUT"] = 5.0
    # Fall back to a local SentenceTransformer (lazy) when the remote embedder
    # errors.  False ⇒ remote failure degrades to sparse-only — the required
    # setting on Render free tier (a local torch build would OOM 512 MB).
    app.config["RAG_EMBED_REMOTE_FALLBACK"] = (
        os.environ.get("RAG_EMBED_REMOTE_FALLBACK", "true").lower() != "false"
    )
    # Qdrant-side BM25 (server-side sparse inference, ``Qdrant/bm25``): when
    # on, the sparse retriever sends the raw query text and the cluster
    # computes the BM25 vector in-cluster — removing the last local model
    # (fastembed) from the query path.  Verified live 2026-08-16 against the
    # provisioned cluster; free on the free tier.  Requires qdrant-client
    # >= 1.12 and a collection whose ``text_sparse`` vector has
    # ``modifier: idf`` (both true for fssai_legal_768).  Default off — opt-in.
    app.config["RAG_QDRANT_BM25"] = os.environ.get("RAG_QDRANT_BM25", "false").lower() == "true"
    # KG contract fusion (2026-08-12, validated by the offline fusion
    # experiment): when true, the generation pipeline runs the graph-RAG
    # retrieval contract (query -> provisions via kg.queries.provisions_for_query)
    # and RRF-fuses those provisions into the ranked context alongside the
    # retrieved chunks — the production equivalent of eval arm G
    # (RRF(dense, sparse, KG-contract)).  Best-effort: a missing/unreachable
    # Neo4j degrades to no KG fusion.
    app.config["RAG_KG_FUSION"] = os.environ.get("RAG_KG_FUSION", "false").lower() == "true"
    # LangGraph agent pipeline (M3, docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md
    # Part C): when true, POST /api/rag/query/agent runs the self-correcting
    # graph (classify → retrieve → generate → verify → expand-and-retry on
    # low groundedness).  Default false — the endpoint then delegates to the
    # legacy pipeline, and /api/rag/query is never affected.
    app.config["RAG_USE_AGENT_PIPELINE"] = os.environ.get("RAG_USE_AGENT_PIPELINE", "false").lower() == "true"
    # M5 human-in-the-loop (2026-08-16): when true, the agent graph pauses
    # at a review interrupt before finalize (POST /api/rag/query/agent → 202
    # awaiting_review; POST /api/rag/query/agent/resume with approved bool).
    # Default false — the graph runs end-to-end.  Requires a checkpointer
    # for resume (RAG_AGENT_CHECKPOINTER).
    app.config["RAG_AGENT_HITL"] = os.environ.get("RAG_AGENT_HITL", "false").lower() == "true"
    # M5 checkpointer (2026-08-16): "memory" (default — MemorySaver, dev/
    # tests, in-process only) or "postgres" (PostgresSaver against
    # DATABASE_URL; requires langgraph-checkpoint-postgres + psycopg-binary).
    app.config["RAG_AGENT_CHECKPOINTER"] = os.environ.get("RAG_AGENT_CHECKPOINTER", "memory").lower()
    # Query-type-aware reranking (CE_RERANK_REVIEW, STEP 7): when true, the
    # ensemble reranker classifies each query into a legal type (prohibition,
    # authority, cross-reference, offence, etc.) and applies the matching
    # per-type QueryTypeConfig weight overrides (e.g. prohibition → no
    # hierarchy boost, authority → larger CE head).  Default true.
    app.config["RAG_LEGAL_QUERY_TYPING"] = os.environ.get("RAG_LEGAL_QUERY_TYPING", "true").lower() != "false"
    # Max KG provisions injected into the LLM context (each provision takes
    # one context slot, displacing the tail of retrieved chunks).
    try:
        app.config["RAG_KG_MAX_PROVISIONS"] = int(os.environ.get("RAG_KG_MAX_PROVISIONS", "5"))
    except ValueError:
        app.config["RAG_KG_MAX_PROVISIONS"] = 5

    # ------------------------------------------------------------------
    # Security headers & HTTPS enforcement via Flask-Talisman
    # ------------------------------------------------------------------
    # Render terminates TLS at the edge.  We use ProxyFix so Flask/Talisman
    # trust the X-Forwarded-Proto header and don't create a redirect loop.
    # ------------------------------------------------------------------
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    csp = {
        "default-src": ["'self'"],
        "style-src": [
            "'self'",
            "'unsafe-inline'",
            "https://fonts.googleapis.com",
            "https://cdnjs.cloudflare.com",
        ],
        "font-src": [
            "'self'",
            "https://fonts.gstatic.com",
            "https://cdnjs.cloudflare.com",
        ],
        "script-src": [
            "'self'",
            "'unsafe-inline'",
        ],
        "img-src": [
            "'self'",
            "data:",
        ],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "base-uri": ["'self'"],
    }

    talisman.init_app(
        app,
        force_https=is_production,
        force_https_permanent=is_production,
        content_security_policy=csp,
        content_security_policy_report_only=False,
        content_security_policy_report_uri="/csp-report",
        strict_transport_security=is_production,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=is_production,
        session_cookie_secure=is_production,
        session_cookie_http_only=True,
        session_cookie_samesite="Lax",
    )

    # Initialize CSRF protection (uses SECRET_KEY set above)
    csrf.init_app(app)

    # Initialize SQLAlchemy database
    db.init_app(app)

    # Initialize security extensions
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    talisman.init_app(app, force_https=False)

    # Initialize Flask-Migrate
    Migrate(app, db)

    # ------------------------------------------------------------------
    # Flask-Login: user_loader callback
    # ------------------------------------------------------------------
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    login_manager.init_app(app)

    # ------------------------------------------------------------------
    # Global login gate — every route requires authentication UNLESS it
    # is one of the public endpoints listed below.
    # ------------------------------------------------------------------
    public_endpoints = {
        "auth.login",
        "static",
        "health.health",
        # Lookup endpoints - public for form prefill/autocomplete
        "case_file_generator.lookup_sample",
        "case_file_generator.list_samples_for_datalist",
        "case_file_generator.lookup_fssai_route",
        "adjudication.lookup_ce_route",
        "adjudication.lookup_fssai_route",
        "inspection.lookup_ce_route",
        "inspection.lookup_fssai_route",
        "sample.lookup_retailer",
        "bill_generator.lookup_fbo_issues",
        "adjudication.lookup_fbo_issues",
        # QStash webhook — authenticated by Upstash-Signature, not session
        "tasks_webhook.run_task",
        # QStash failure callback — authenticated by Upstash-Signature, not session
        "tasks_webhook.delivery_failed",
        # RAG health probe — public for monitoring
        "rag.health",
    }

    @app.before_request
    def set_audit_user():
        """Store the current user ID on ``db.session.info`` so that audit
        event hooks can read it without depending on the request context.
        """
        try:
            db.session.info["audit_user_id"] = current_user.get_id() if current_user.is_authenticated else None
        except (RuntimeError, AttributeError):
            db.session.info["audit_user_id"] = None

    @app.before_request
    def require_login():
        if request.endpoint and request.endpoint not in public_endpoints and not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))

    # Register custom Jinja filters globally
    from app.utils.filters import format_date_indian, to_words

    app.jinja_env.filters["to_words"] = to_words
    app.jinja_env.filters["format_date"] = format_date_indian
    app.jinja_env.filters["format_date_indian"] = format_date_indian

    # Flask-Login already exposes current_user in all templates,
    # no need for a custom context_processor.

    # ------------------------------------------------------------------
    # Wire up SQLAlchemy audit event hooks for Adjudication, Bill, CaseFile
    # ------------------------------------------------------------------
    from app.audit_hooks import register_audit_hooks

    register_audit_hooks()

    # ------------------------------------------------------------------
    # Wire up SQLite FTS5 search event hooks (auto-index on CRUD)
    # ------------------------------------------------------------------
    from app.search.indexer import register_search_hooks

    register_search_hooks()

    # ------------------------------------------------------------------
    # Wire up Qdrant vector-store event hooks (Agent A Phase 1, Day 3).
    # Inert until chunk/document models are registered via
    # app.rag.qdrant_indexer.register_chunk_model / register_document_model
    # (planned LegalChunk / LegalDocument models — Phase 3, Day 12).
    # ------------------------------------------------------------------
    from app.rag.qdrant_indexer import register_qdrant_hooks

    register_qdrant_hooks()

    # Register blueprints (auth first so login page is available)
    from app.adjudication.routes import adjudication_bp
    from app.annexure import annexure_bp
    from app.audit import audit_bp
    from app.auth.routes import auth_bp
    from app.bill_generator.routes import bill_generator_bp
    from app.billing.routes import billing_bp
    from app.case_file_generator.routes import case_file_generator_bp
    from app.fbo_issue.routes import fbo_issue_bp
    from app.food_cell import food_cell_bp
    from app.health import health_bp
    from app.inspection.routes import inspection_bp
    from app.knowledge_graph import kg_bp
    from app.legal_analysis import legal_analysis_bp
    from app.sample.routes import sample_bp
    from app.search import search_bp
    from app.settings.routes import settings_bp
    from app.tasks_webhook import tasks_webhook_bp
    from app.timeline import timeline_bp
    from app.validation import validation_bp
    from app.version_control import version_control_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(case_file_generator_bp, url_prefix="/case_file_generator")
    app.register_blueprint(adjudication_bp, url_prefix="/adjudication")
    from app.document_viewer import document_viewer_bp

    app.register_blueprint(document_viewer_bp, url_prefix="/document_viewer")
    from app.evidence import evidence_bp

    app.register_blueprint(evidence_bp, url_prefix="/evidence")
    app.register_blueprint(bill_generator_bp, url_prefix="/bill_generator")
    app.register_blueprint(fbo_issue_bp, url_prefix="/fbo-issue")
    app.register_blueprint(sample_bp, url_prefix="/sample")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(inspection_bp, url_prefix="/inspection")
    app.register_blueprint(legal_analysis_bp, url_prefix="/legal")
    app.register_blueprint(audit_bp, url_prefix="/admin")
    app.register_blueprint(version_control_bp)
    app.register_blueprint(tasks_webhook_bp)
    app.register_blueprint(search_bp, url_prefix="/search")
    app.register_blueprint(annexure_bp, url_prefix="/annexure")
    app.register_blueprint(validation_bp, url_prefix="/validation")
    app.register_blueprint(health_bp)
    app.register_blueprint(food_cell_bp, url_prefix="/food-cell")
    app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")
    from app.ai_assistant import ai_bp

    app.register_blueprint(ai_bp, url_prefix="/ai-assistant")
    # timeline_bp carries its own url_prefix ("/timeline") in the Blueprint.
    app.register_blueprint(timeline_bp)
    # RAG blueprint (Phase 1: retrieval foundation + health endpoint)
    from app.rag import rag_bp

    app.register_blueprint(rag_bp)

    # Initialize database tables (models must be imported first)
    # Import models so they're registered with SQLAlchemy metadata
    from app import models

    # Fallback safeguard: if core tables are missing (e.g., fresh local DB
    # without migrations applied), create them so startup sync doesn't fail.
    # On a FRESH database we also stamp the Alembic head: the historical
    # migration chain was written as incremental patches on top of a
    # db.create_all()-created schema (e.g. the baseline adds columns to
    # tables that don't exist yet from migrations alone), so replaying it
    # against a fresh DB crashes on duplicate columns. Stamping makes the
    # subsequent `flask db upgrade` in the Render start command a no-op
    # while future migrations still apply normally.
    with app.app_context():
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
        inspector = sa_inspect(engine)
        if "fso" not in inspector.get_table_names():
            db.create_all()
            app.logger.info("Created missing tables via db.create_all() fallback")
            # Only stamp when there is NO migration history at all — never
            # clobber a partially-migrated database.
            if "alembic_version" not in inspector.get_table_names():
                try:
                    from flask_migrate import stamp as alembic_stamp

                    alembic_stamp(revision="head")
                    app.logger.info("Stamped fresh database at migration head")
                except (Exception, SystemExit) as exc:
                    app.logger.warning(
                        "Could not stamp fresh database at migration head (%s) — "
                        "`flask db upgrade` may replay the full chain next deploy.",
                        exc,
                    )
            # Existing database — self-heal tables that `flask db upgrade`
            # can NEVER create: a migration inserted mid-chain (e.g. the
            # Phase 18 `a1b2c3d4e5f6` role/user_roles/comment migration) is an
            # ancestor of the DB's current version, so Alembic never replays it
            # and its tables stay missing (login crashed with
            # `relation "user_roles" does not exist`). create_all() is only
            # safe here when the DB is stamped at head — then no migration is
            # pending that could later collide with the created tables.
            try:
                from alembic.config import Config as AlembicConfig
                from alembic.script import ScriptDirectory
                from sqlalchemy import text

                migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
                alembic_cfg = AlembicConfig(str(migrations_dir / "alembic.ini"))
                alembic_cfg.set_main_option("script_location", str(migrations_dir))
                if "alembic_version" in inspector.get_table_names():
                    with engine.connect() as conn:
                        db_version = conn.execute(
                            text("SELECT version_num FROM alembic_version"),
                        ).scalar()
                    if db_version and db_version == ScriptDirectory.from_config(alembic_cfg).get_current_head():
                        before = set(inspector.get_table_names())
                        # Concurrent boots (web + Celery worker) may both reach
                        # this; create_all only adds genuinely missing tables and
                        # a duplicate-CREATE race is caught below (non-fatal).
                        db.create_all()
                        created = sorted(set(sa_inspect(engine).get_table_names()) - before)
                        if created:
                            app.logger.warning(
                                "Schema self-heal: created missing model tables %s (DB stamped at "
                                "migration head — `flask db upgrade` cannot replay mid-chain "
                                "insertions).",
                                created,
                            )
            except Exception as exc:
                app.logger.warning("Schema self-heal skipped: %s", exc)

        # Create FTS5 search virtual table on SQLite (no-op on PostgreSQL).
        # This runs unconditionally so the table exists even on a pre-existing
        # database that predates the search feature.
        from app.search.indexer import ensure_search_table

        ensure_search_table()

    # FSO sync on startup - import and run sync in app context
    # This ensures FSO names are available as soon as the app starts
    # Can be skipped via SKIP_FSO_STARTUP_SYNC env var (e.g. for fresh-DB migrations)
    if not os.environ.get("SKIP_FSO_STARTUP_SYNC"):
        from app.utils.fso_data import sync_fso_from_markdown

        with app.app_context(), _fso_sync_lock:
            try:
                result = sync_fso_from_markdown()
                if result["errors"]:
                    app.logger.warning(f"FSO startup sync completed with warnings: {result['errors']}")
                else:
                    app.logger.info(f"FSO startup sync: {result['inserted']} inserted, {result['updated']} updated")
            except Exception as e:
                app.logger.error(f"FSO startup sync failed: {e!s}")

    # Redirect root to first tab (Sample -adjudication)
    @app.route("/")
    def root():
        return redirect(url_for("case_file_generator.index"))

    # QStash daily backup schedule for Multi-Target Sheets Redundancy (Priority 7)
    if os.environ.get("ENABLE_BACKUP_SCHEDULE", "false").lower() == "true":
        try:
            from app.utils.qstash_client import publish_recurring

            result = publish_recurring(
                "backup_redundant_sheets",
                schedule="0 2 * * *",  # daily at 02:00 UTC
                payload={},
            )
            app.logger.info("Registered daily backup schedule with QStash: %s", result)
        except Exception as e:
            app.logger.warning(f"QStash backup schedule registration failed: {e}")

    # QStash daily corpus-ingestion schedule (Agent A Phase 1, Day 4).
    # Dispatches rag.ingest_corpus_task against RAG_CORPUS_DIR. Requires
    # QStash credentials (paid plan) — publish_recurring returns
    # {"mode": "disabled"} gracefully when unconfigured.
    rag_corpus_dir = os.environ.get("RAG_CORPUS_DIR")
    if os.environ.get("RAG_ENABLE_INGESTION_SCHEDULE", "false").lower() == "true" and rag_corpus_dir:
        try:
            from app.utils.qstash_client import publish_recurring

            result = publish_recurring(
                "ingest_corpus",
                schedule=os.environ.get("RAG_INGESTION_CRON", "0 3 * * *"),  # daily 03:00 UTC
                payload={"corpus_dir": rag_corpus_dir},
            )
            app.logger.info("Registered daily RAG corpus-ingestion schedule with QStash: %s", result)
        except Exception as e:
            app.logger.warning(f"QStash RAG ingestion schedule registration failed: {e}")

    # Initialize Celery with Flask app context support
    # Lazy import to avoid ModuleNotFoundError in deployment environments
    try:
        from celery_app import make_celery

        app.celery = make_celery(app)
    except ImportError:
        # Celery not available (e.g., in minimal deployment)
        app.celery = None

    return app


# Create the Flask application instance for Gunicorn
app = create_app()

# Export celery at module level so it can be imported elsewhere
celery = app.celery
