"""FTS5 search index management for NSA Webservice.

Creates and maintains a SQLite FTS5 virtual table (search_index) that
indexes searchable text from four entity types:

  - case_file    -- CaseFile: case_number, manufacturer/retailer names,
                    product name, batch, sample code, sections, etc.
  - adjudication -- Adjudication: case_number, FBO name/address,
                    concerned food, problem description, etc.
  - annexure     -- Annexure: caption, OCR text, tags.
  - evidence     -- Evidence: caption, OCR text, tags, evidence type.

The index is automatically kept in sync via SQLAlchemy after_flush event
hooks (following the same pattern as app/audit_hooks.py).  A index_all()
function is also provided for manual full re-indexing.

On PostgreSQL (production) FTS5 is unavailable; the search API transparently
falls back to LIKE queries against the regular ORM tables.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from app.extensions import db

logger = logging.getLogger(__name__)

ENTITY_CASE_FILE = "case_file"
ENTITY_ADJUDICATION = "adjudication"
ENTITY_ANNEXURE = "annexure"
ENTITY_EVIDENCE = "evidence"

ENTITY_TYPES = frozenset(
    {
        ENTITY_CASE_FILE,
        ENTITY_ADJUDICATION,
        ENTITY_ANNEXURE,
        ENTITY_EVIDENCE,
    }
)

_FTS_TABLE = "search_index"

_CREATE_FTS_SQL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS " + _FTS_TABLE + " USING fts5(\n"
    "    entity_type UNINDEXED,\n"
    "    entity_id   UNINDEXED,\n"
    "    title,\n"
    "    content,\n"
    "    tokenize = 'porter unicode61'\n"
    ")"
)

_BUILDERS: dict[str, Callable[[Any], tuple[str, str]]] = {}

# ---------------------------------------------------------------------------
# Per-entity document builders -> (title, content)
# ---------------------------------------------------------------------------


def _case_file_document(cf) -> tuple[str, str]:
    """Build the search document for a CaseFile record."""
    title = cf.case_number or str(cf.id)
    parts = [
        cf.case_number,
        cf.food_safety_officer_name,
        cf.manufacturer_name,
        cf.manufacturer_fbo_name,
        cf.manufacturer_address,
        cf.manufacturer_fssai,
        cf.retailer_name,
        cf.retailer_fbo_name,
        cf.retailer_address,
        cf.retailer_fssai,
        cf.product_name,
        cf.batch_no,
        cf.sample_code,
        cf.applicable_regulation,
        cf.applicable_clause,
        cf.applicable_sections,
        cf.analyst_report_no,
        cf.directive_letter_no,
    ]
    content = " ".join(str(p) for p in parts if p)
    return title, content


def _adjudication_document(adj) -> tuple[str, str]:
    """Build the search document for an Adjudication record."""
    title = adj.case_number or str(adj.id)
    parts = [
        adj.case_number,
        adj.food_safety_officer,
        adj.fbo_owner,
        adj.fbo_name,
        adj.fbo_address,
        adj.fssai_license,
        adj.ce_license_no,
        adj.ce_trade_name,
        adj.ce_proprietor,
        adj.ce_address,
        adj.ce_status,
        adj.concerned_food,
        adj.problem,
    ]
    content = " ".join(str(p) for p in parts if p)
    return title, content


def _annexure_document(a) -> tuple[str, str]:
    """Build the search document for an Annexure record."""
    title = a.caption or str(a.id)
    parts = [a.caption, a.ocr_text, a.tags]
    content = " ".join(str(p) for p in parts if p)
    return title, content


def _evidence_document(e) -> tuple[str, str]:
    """Build the search document for an Evidence record."""
    title = e.caption or e.evidence_type or str(e.id)
    parts = [e.caption, e.ocr_text, e.tags, e.evidence_type]
    content = " ".join(str(p) for p in parts if p)
    return title, content


_BUILDERS[ENTITY_CASE_FILE] = _case_file_document
_BUILDERS[ENTITY_ADJUDICATION] = _adjudication_document
_BUILDERS[ENTITY_ANNEXURE] = _annexure_document
_BUILDERS[ENTITY_EVIDENCE] = _evidence_document


# ---------------------------------------------------------------------------
# Model resolver & dialect helper
# ---------------------------------------------------------------------------


def _resolve_model(entity_type: str):
    """Lazily resolve the SQLAlchemy model class for an entity type."""
    from app.models import Adjudication, Annexure, CaseFile, Evidence

    class_map = {
        ENTITY_CASE_FILE: CaseFile,
        ENTITY_ADJUDICATION: Adjudication,
        ENTITY_ANNEXURE: Annexure,
        ENTITY_EVIDENCE: Evidence,
    }
    return class_map.get(entity_type)


def _dialect() -> str:
    """Return the current database dialect name."""
    try:
        return str(db.session.get_bind().dialect.name)
    except Exception:
        return "unknown"


def ensure_search_table() -> bool:
    """Create the FTS5 virtual table if it does not exist.

    SQLite only.  On PostgreSQL this is a no-op -- the search API falls
    back to LIKE queries on the regular ORM tables.

    Must be called within an application context.
    """
    if _dialect() != "sqlite":
        logger.info("FTS5 table creation skipped (dialect: %s)", _dialect())
        return False
    db.session.execute(text(_CREATE_FTS_SQL))
    db.session.commit()
    return True


# ---------------------------------------------------------------------------
# Low-level index operations (no commit -- caller controls transaction)
# ---------------------------------------------------------------------------


def _upsert_row(entity_type, entity_id, title, content):
    """Delete then insert a row for the given entity (no commit)."""
    db.session.execute(
        text("DELETE FROM " + _FTS_TABLE + " WHERE entity_type = :etype AND entity_id = :eid"),  # noqa: S608
        {"etype": entity_type, "eid": entity_id},
    )
    db.session.execute(
        text(
            "INSERT INTO " + _FTS_TABLE + "(entity_type, entity_id, title, content) "
            "VALUES (:etype, :eid, :title, :content)"
        ),
        {"etype": entity_type, "eid": entity_id, "title": title, "content": content},
    )


def _delete_row(entity_type, entity_id):
    """Delete a row from the FTS index (no commit)."""
    db.session.execute(
        text("DELETE FROM " + _FTS_TABLE + " WHERE entity_type = :etype AND entity_id = :eid"),  # noqa: S608
        {"etype": entity_type, "eid": entity_id},
    )


def _build_doc(target, entity_type):
    """Dispatch to the correct document builder based on entity_type."""
    builder = _BUILDERS.get(entity_type)
    if builder is None:
        logger.warning("No builder for entity_type: %s", entity_type)
        return str(target.id), ""
    return builder(target)


# ---------------------------------------------------------------------------
# Public indexing API
# ---------------------------------------------------------------------------


def index_record(entity_type, entity_id):
    """Index (or re-index) a single record and commit.

    Returns True if the record was indexed, False if not found or
    entity_type is unknown.
    """
    if entity_type not in ENTITY_TYPES:
        logger.warning("Cannot index unknown entity_type: %s", entity_type)
        return False

    model = _resolve_model(entity_type)
    if model is None:
        return False

    try:
        pk = int(entity_id)
    except (ValueError, TypeError):
        pk = entity_id

    record = db.session.get(model, pk)
    if record is None:
        logger.warning(
            "Cannot index %s id=%s: not found, removing from index",
            entity_type,
            entity_id,
        )
        delete_from_index(entity_type, entity_id)
        return False

    title, content = _build_doc(record, entity_type)
    _upsert_row(entity_type, str(record.id), title, content)
    db.session.commit()
    return True


def delete_from_index(entity_type, entity_id):
    """Remove an entity from the FTS index and commit."""
    _delete_row(entity_type, str(entity_id))
    db.session.commit()


def index_all():
    """Rebuild the entire FTS5 index from all records.

    Returns the total number of records indexed.
    """
    if _dialect() != "sqlite":
        logger.warning("index_all() skipped (non-SQLite dialect: %s)", _dialect())
        return 0

    ensure_search_table()
    db.session.execute(text("DELETE FROM " + _FTS_TABLE))  # noqa: S608

    total = 0
    for entity_type, builder in _BUILDERS.items():
        model = _resolve_model(entity_type)
        if model is None:
            continue
        records = db.session.execute(db.select(model)).scalars().all()
        for record in records:
            title, content = builder(record)
            db.session.execute(
                text(
                    "INSERT INTO " + _FTS_TABLE + "(entity_type, entity_id, title, content) "
                    "VALUES (:etype, :eid, :title, :content)"
                ),
                {
                    "etype": entity_type,
                    "eid": str(record.id),
                    "title": title,
                    "content": content,
                },
            )
            total += 1

    db.session.commit()
    return total


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search(query, entity_type=None, limit=20):
    """Search the index for query.

    Uses SQLite FTS5 when available (SQLite backend).  Falls back to
    ILIKE queries on regular tables when FTS5 is unavailable
    (PostgreSQL).

    Returns a list of dicts with keys: entity_type, entity_id, title,
    snippet.
    """
    if not query or not query.strip():
        return []

    if _dialect() == "sqlite":
        return _search_fts5(query, entity_type, limit)
    return _search_like(query, entity_type, limit)


def _search_fts5(query, entity_type, limit):
    """Full-text search via FTS5 MATCH + bm25() ranking."""
    try:
        ensure_search_table()
        sql = (
            "SELECT entity_type, entity_id, title, "
            "snippet(" + _FTS_TABLE + ", 3, '<mark>', '</mark>', '&hellip;', 15) AS snippet "
            "FROM " + _FTS_TABLE + " WHERE " + _FTS_TABLE + " MATCH :q"
        )
        params = {"q": query}
        if entity_type:
            sql += " AND entity_type = :etype"
            params["etype"] = entity_type
        sql += " ORDER BY bm25(" + _FTS_TABLE + ") LIMIT :limit"
        params["limit"] = limit

        rows = db.session.execute(text(sql), params).fetchall()
        return [
            {
                "entity_type": row[0],
                "entity_id": row[1],
                "title": row[2],
                "snippet": (row[3] or "").replace("\n", " ").strip(),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("FTS5 search failed (%s), falling back to LIKE", exc)
        return _search_like(query, entity_type, limit)


def _search_like(query, entity_type, limit):
    """Fallback search using ILIKE on regular tables (PostgreSQL)."""
    from app.models import Adjudication, Annexure, CaseFile, Evidence

    if not query or not query.strip():
        return []

    search_term = "%" + query + "%"
    model_map = {
        ENTITY_CASE_FILE: (
            CaseFile,
            [
                "case_number",
                "manufacturer_name",
                "retailer_name",
                "product_name",
                "applicable_sections",
                "applicable_regulation",
            ],
        ),
        ENTITY_ADJUDICATION: (
            Adjudication,
            [
                "case_number",
                "fbo_name",
                "concerned_food",
                "problem",
                "ce_trade_name",
                "fssai_license",
            ],
        ),
        ENTITY_ANNEXURE: (Annexure, ["caption", "ocr_text", "tags"]),
        ENTITY_EVIDENCE: (Evidence, ["caption", "ocr_text", "tags", "evidence_type"]),
    }

    results = []
    for etype, (model, columns) in model_map.items():
        if entity_type and entity_type != etype:
            continue
        conditions = []
        for col_name in columns:
            col = getattr(model, col_name, None)
            if col is not None:
                conditions.append(col.ilike(search_term))
        if not conditions:
            continue
        for row in db.session.query(model).filter(db.or_(*conditions)).limit(limit):
            title = (
                getattr(row, "case_number", None)
                or getattr(row, "caption", None)
                or getattr(row, "evidence_type", None)
                or str(row.id)
            )
            snippet_text = (
                getattr(row, "concerned_food", None)
                or getattr(row, "problem", None)
                or getattr(row, "ocr_text", None)
                or getattr(row, "caption", None)
                or ""
            )
            results.append(
                {
                    "entity_type": etype,
                    "entity_id": str(row.id),
                    "title": title,
                    "snippet": snippet_text[:200] if snippet_text else "",
                }
            )

    return results


# ---------------------------------------------------------------------------
# SQLAlchemy event hooks -- auto-index on insert / update / delete
# ---------------------------------------------------------------------------

_registered = False

_INDX_MODELS = None


def _entity_type_for(model_cls):
    """Reverse-lookup the entity-type string for a model class."""
    from app.models import Adjudication, Annexure, CaseFile, Evidence

    reverse = {
        CaseFile: ENTITY_CASE_FILE,
        Adjudication: ENTITY_ADJUDICATION,
        Annexure: ENTITY_ANNEXURE,
        Evidence: ENTITY_EVIDENCE,
    }
    return reverse.get(model_cls)


def _get_indexed_models():
    """Lazily import and cache the tuple of indexed model classes."""
    global _INDX_MODELS
    if _INDX_MODELS is None:
        from app.models import Adjudication, Annexure, CaseFile, Evidence

        _INDX_MODELS = (CaseFile, Adjudication, Annexure, Evidence)
    return _INDX_MODELS


def _on_after_flush(session, _flush_context):
    """Auto-index changed records after each session flush.

    Uses db.session.execute() (same transaction) so that FTS5 updates
    are committed/rolled-back together with the calling transaction.
    Any FTS5 errors are caught and logged so they never break the
    outer transaction.
    """
    if not (session.new or session.dirty or session.deleted):
        return

    try:
        if _dialect() != "sqlite":
            return  # FTS5 is SQLite-only

        indexed_models = _get_indexed_models()

        # Inserts + Updates
        for target in session.new | session.dirty:
            if isinstance(target, indexed_models):
                entity_type = _entity_type_for(type(target))
                if entity_type:
                    title, content = _build_doc(target, entity_type)
                    _upsert_row(entity_type, str(target.id), title, content)

        # Deletes
        for target in session.deleted:
            if isinstance(target, indexed_models):
                entity_type = _entity_type_for(type(target))
                if entity_type:
                    _delete_row(entity_type, str(target.id))

    except Exception as exc:
        logger.warning("Search index auto-update failed: %s", exc)


def register_search_hooks():
    """Wire the after_flush event listener on db.session.

    Must be called after db.init_app(app) and within an app context.
    Idempotent -- safe to call multiple times.
    """
    global _registered
    if _registered:
        return
    from sqlalchemy.event import listen
    from sqlalchemy.orm import Session as _SQLASession

    listen(_SQLASession, "after_flush", _on_after_flush)
    _registered = True
