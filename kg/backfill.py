"""Corpus-wide ``(act, section) -> body`` index for KG evidence.

The KG fusion path needs quotable statutory text, but graph ``LegalProvision``
nodes can be pointer-only (a section reference) while the real body lives in
the corpus chunk store.  A per-request index built from the current vector
top-k cannot fix that: the provisions most in need of backfill are exactly the
ones the vector pool did *not* retrieve.  This module builds the index from the
corpus payload cache or the ``LegalChunk`` table instead, and caches it for the
process.

Keys are produced by :func:`kg.hybrid.backfill_key`, so graph names such as
``fssai:s57`` and corpus values such as ``57`` resolve to the same entry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Process-local cache.  ``None`` means "not loaded yet"; ``{}`` means
#: "loaded and empty" (so a missing corpus is not re-scanned on every query).
_CACHE: dict[tuple[str, str], str] | None = None


def build_index_from_payloads(
    payloads: Iterable[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    """Build a ``(normalised act, normalised section) -> body`` index.

    Payloads may be Qdrant payload dicts, ``LegalChunk.metadata_json`` dicts, or
    the lightweight dicts produced by :func:`build_index_from_chunks`.  The
    longest body wins when several chunks cover the same provision.
    """
    from kg.hybrid import backfill_key_variants

    index: dict[tuple[str, str], str] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        act = str(
            payload.get("act_name")
            or payload.get("document_title")
            or ""
        ).strip()
        text = str(payload.get("chunk_text") or payload.get("text") or "").strip()
        if not act or not text:
            continue

        sections: list[str] = []
        direct = payload.get("section_number") or payload.get("clause_number")
        if direct:
            sections.append(str(direct))
        for span in payload.get("provision_spans") or []:
            if isinstance(span, dict) and span.get("section"):
                sections.append(str(span["section"]))
        for covered in payload.get("sections_covered") or []:
            if covered:
                sections.append(str(covered))
        for provision_id in payload.get("provision_ids") or []:
            if provision_id:
                # ``fssai:s57`` -> ``s57``; backfill_key_variants strips the
                # alpha prefix on lookup, but the index key keeps the graph
                # form so exact matches win.
                sections.append(str(provision_id).split(":")[-1])

        for section in sections:
            for key in backfill_key_variants(act, section):
                if not key[0] or not key[1]:
                    continue
                if len(text) > len(index.get(key, "")):
                    index[key] = text
    return index


def build_index_from_chunks(chunks: Iterable[Any]) -> dict[tuple[str, str], str]:
    """Build an index from already-retrieved ``RetrievedChunk`` objects.

    This is the fallback when no corpus-wide index is available.  It is
    intentionally *not* the primary source: it can only cover provisions that
    the vector pool already retrieved.
    """
    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        payloads.append(
            {
                "act_name": getattr(chunk, "act_name", "")
                or getattr(chunk, "document_title", ""),
                "document_title": getattr(chunk, "document_title", ""),
                "section_number": getattr(chunk, "section_number", ""),
                "clause_number": getattr(chunk, "clause_number", ""),
                "chunk_text": getattr(chunk, "text", ""),
            }
        )
    return build_index_from_payloads(payloads)


def _load_payload_cache() -> dict[tuple[str, str], str]:
    """Load the evaluation payload cache when it is present.

    Production does not normally ship ``evaluation/out``; this path keeps
    offline A/B runs and local reproduction consistent with the DB path.
    """
    path = (
        Path(__file__).resolve().parent.parent
        / "evaluation"
        / "out"
        / "cache"
        / "payload_index.jsonl"
    )
    if not path.exists():
        return {}
    try:
        payloads: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                payload = item.get("payload") if isinstance(item, dict) else None
                if isinstance(payload, dict):
                    payloads.append(payload)
        return build_index_from_payloads(payloads)
    except Exception as exc:  # pragma: no cover - corrupt cache is non-fatal
        logger.warning("KG backfill payload cache load failed: %s", exc)
        return {}


def _load_db_index() -> dict[tuple[str, str], str] | None:
    """Build the index from ``LegalChunk`` rows, when a Flask app is active.

    Returns ``None`` when no app context exists (so the caller can avoid
    caching an empty index before the app is ready) and ``{}`` when the table
    is genuinely empty.
    """
    try:
        from flask import has_app_context

        if not has_app_context():
            return None
        from app.extensions import db
        from app.models import LegalChunk

        rows = db.session.execute(
            db.select(
                LegalChunk.id,
                LegalChunk.text,
                LegalChunk.section_number,
                LegalChunk.metadata_json,
            )
        ).all()
    except Exception as exc:
        logger.warning("KG backfill DB index unavailable: %s", exc)
        return None

    payloads: list[dict[str, Any]] = []
    for chunk_id, text, section_number, metadata in rows:
        meta = metadata if isinstance(metadata, dict) else {}
        if isinstance(metadata, str):
            try:
                parsed = json.loads(metadata)
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                meta = parsed
        payloads.append(
            {
                **meta,
                "chunk_id": chunk_id,
                "chunk_text": text or meta.get("chunk_text") or "",
                "section_number": section_number or meta.get("section_number"),
            }
        )
    return build_index_from_payloads(payloads)


def load_corpus_index() -> dict[tuple[str, str], str]:
    """Return the cached corpus-wide backfill index.

    Resolution order: payload cache (offline/eval) -> ``LegalChunk`` table ->
    empty.  The result is cached for the process lifetime; call
    :func:`clear_corpus_index_cache` after a corpus re-ingest.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    index = _load_payload_cache()
    if not index:
        db_index = _load_db_index()
        if db_index is None:
            # No app context yet: retry on the next call rather than caching
            # an empty index for the life of the process.
            return {}
        index = db_index
    _CACHE = index
    logger.info("KG backfill corpus index entries=%d", len(index))
    return index


def clear_corpus_index_cache() -> None:
    """Drop the process-local index (tests and post-ingestion refreshes)."""
    global _CACHE
    _CACHE = None
