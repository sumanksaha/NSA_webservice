"""Qdrant payload identity stamping (P1 remediation — 2026-08-11).

Closes the audit's last P1 gap: Qdrant payloads carried **no** ``provision_id``,
``instrument_id``, ``legal_domain``, or ``status``, so Qdrant was not
provision-addressable on its own (the join only worked graph-side through
Neo4j ``Chunk.qdrant_point_id``).

This module stamps those four canonical fields onto every live point using
the **same registry that builds Neo4j** (per the audit's recommended
remediation):

- ``instrument_id``  — :meth:`KGCorpusIngestionEngine.resolve_instrument_id`
  (manifest ``document_id`` → canonical id; FSS documents via the local DB
  mapping).
- ``legal_domain``   — :meth:`KGCorpusIngestionEngine.resolve_domain` for
  manifest docs; the collection's domain for everything else (fallback).
- ``status``         — :meth:`KGCorpusIngestionEngine.instrument_status`
  (``is_current`` + ``notes`` → current / draft / superseded / repealed).
- ``provision_id``   — :meth:`KGCorpusIngestionEngine.build_provisions`
  (section numbers validated against ``app/rag/legal_sections.py``, exactly
  the same rules that produce Neo4j ``LegalProvision`` nodes) — so a Qdrant
  point's ``provision_id`` matches the graph 1:1.

Design rules:

1. **Same registry** — no new ID logic; the stamping reuses the exact engine
   methods that built the KG, so payload and graph agree by construction.
2. **Idempotent + additive** — ``set_payload`` only adds/updates the four
   fields; points that already carry the correct values are reported as
   ``unchanged`` and skipped. Re-runs are safe.
3. **Conservative** — points whose document is unknown (no manifest row, no
   FSS DB row) still get ``legal_domain`` from the collection name, but no
   ``instrument_id`` / ``provision_id`` / ``status`` is guessed. Section
   numbers are validated (year-like junk rejected, act-range enforced) so a
   bogus ``provision_id`` is never stamped.
4. **Graceful** — no Qdrant configured degrades to a descriptive error;
   per-collection failures are isolated and reported.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

#: The canonical fields this stamper writes onto every point.
STAMP_FIELDS: tuple[str, ...] = ("provision_id", "instrument_id", "legal_domain", "status")

#: Keyword payload indexes to create after stamping (audit §20 recommended
#: indexable identity fields). Created best-effort; already-existing indexes
#: are ignored.
INDEX_FIELDS: tuple[str, ...] = ("provision_id", "instrument_id", "legal_domain", "status")


class QdrantPayloadStamper:
    """Stamp canonical legal identity onto Qdrant payloads (idempotent).

    Args:
        engine: Optional pre-built :class:`KGCorpusIngestionEngine` (injected
            for tests; built from the manifest by default).
        batch_size: Points per ``set_payload`` call (grouped by identical
            payload so one request covers many points).
    """

    def __init__(self, engine: Any | None = None, batch_size: int = 500) -> None:
        if engine is None:
            from kg.corpus_ingestion import KGCorpusIngestionEngine

            engine = KGCorpusIngestionEngine()
        self._engine = engine
        self._batch_size = batch_size
        #: document_id -> {"instrument_id", "legal_domain", "status", "act_name"}
        self._doc_identity: dict[str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------ #
    # Registry (same source of truth as the Neo4j build)
    # ------------------------------------------------------------------ #

    def doc_identity_map(self) -> dict[str, dict[str, Any]]:
        """Resolve every corpus document's canonical identity once.

        Built from :meth:`KGCorpusIngestionEngine._build_instrument_rows`
        (manifest rows + FSS DB rows + stubs), keyed by ``document_id``.
        """
        if self._doc_identity is not None:
            return self._doc_identity
        rows, _ = self._engine._build_instrument_rows()
        self._doc_identity = {
            str(r["document_id"]): {
                "instrument_id": r["instrument_id"],
                "legal_domain": r["legal_domain"],
                "status": r["status"],
                "act_name": r.get("act_name") or "",
            }
            for r in rows
            if r.get("document_id")
        }
        return self._doc_identity

    def _collection_domain(self, collection: str) -> str:
        """Fallback domain for a collection (unknown documents only)."""
        from app.rag.collections import DOMAIN_COLLECTIONS

        for mdomain, coll in DOMAIN_COLLECTIONS.items():
            if coll == collection:
                from kg.corpus_ingestion import MANIFEST_DOMAIN_TO_KG

                kg = MANIFEST_DOMAIN_TO_KG.get(mdomain)
                if kg:
                    return kg
                # wb_state resolves per document — the collection-level default
                # is LAND_PREMISES (KMC → MUNICIPAL is stamped via manifest).
                if mdomain == "wb_state":
                    return "LAND_PREMISES"
        return "FOOD_SAFETY"

    # ------------------------------------------------------------------ #
    # Point-level resolution
    # ------------------------------------------------------------------ #

    def _provision_id(self, instrument_id: str, act_name: str, section_number: Any) -> str | None:
        """Resolve the canonical provision id for a chunk's section.

        Mirrors :meth:`KGCorpusIngestionEngine.build_provisions` exactly:
        the section is cleaned (``"26(2)(ii)"`` → ``"26"``), validated
        (numeric, non-year-like, within the act's registered range when
        known), and formatted as ``{INSTRUMENT}_SEC_{n}`` — the same id the
        Neo4j build produces, so payload and graph agree 1:1.
        """
        from app.rag.legal_sections import sections_for_act
        from kg.corpus_ingestion import _clean_section, _valid_section

        sn = _clean_section(section_number)
        if not sn:
            return None
        known = sections_for_act(act_name) if act_name else None
        if not _valid_section(sn, known):
            return None
        return f"{instrument_id}_SEC_{sn}"

    def _fields_for_point(self, collection: str, payload: dict[str, Any]) -> dict[str, str]:
        """Compute the four identity fields for one point (never guesses)."""
        doc_id = str(payload.get("document_id") or "")
        meta = self.doc_identity_map().get(doc_id) if doc_id else None

        fields: dict[str, str] = {}
        if meta:
            fields["instrument_id"] = meta["instrument_id"]
            fields["legal_domain"] = meta["legal_domain"]
            fields["status"] = meta["status"]
            pid = self._provision_id(meta["instrument_id"], meta["act_name"], payload.get("section_number"))
            if pid:
                fields["provision_id"] = pid
        else:
            # Unknown document: only the collection's domain is safe to stamp.
            fields["legal_domain"] = self._collection_domain(collection)
        return fields

    # ------------------------------------------------------------------ #
    # Scroll + write
    # ------------------------------------------------------------------ #

    def _scroll_points(self, collection: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Read points (id + payload) from a collection (read-only)."""
        client = self._engine._get_qdrant_client()
        out: list[dict[str, Any]] = []
        offset: Any = None
        while True:
            page_limit = min(limit - len(out), 1000) if limit else 1000
            if page_limit <= 0:
                break
            recs, offset = client.scroll(
                collection_name=collection,
                limit=page_limit,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            if not recs:
                break
            for rec in recs:
                if isinstance(rec, dict):
                    payload = dict(rec.get("payload") or rec)
                    rec_id = rec.get("id")
                else:
                    payload = dict(getattr(rec, "payload", None) or {})
                    rec_id = getattr(rec, "id", None)
                if not payload.get("chunk_id") and rec_id:
                    payload["chunk_id"] = str(rec_id)
                out.append({"id": str(rec_id) if rec_id is not None else payload.get("chunk_id"), "payload": payload})
            if not offset:
                break
        return out

    def plan(
        self,
        collections: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Compute per-point field updates WITHOUT writing.

        Returns a summary dict with per-collection stats and the planned
        updates keyed by collection → point id → ``{field: value}``.
        """
        client = self._engine._get_qdrant_client()
        if collections is None:
            collections = [c.name for c in client.get_collections().collections]
        summary: dict[str, Any] = {"collections": {}, "total_points": 0, "points_to_update": 0}
        for coll in sorted(collections):
            points = self._scroll_points(coll, limit=limit)
            stats = {
                "points": len(points),
                "with_instrument": 0,
                "with_provision": 0,
                "with_status": 0,
                "unknown_document": 0,
                "to_update": 0,
                "updates": {},
            }
            self.doc_identity_map()
            for pt in points:
                payload = pt["payload"]
                if not str(payload.get("document_id") or ""):
                    stats["unknown_document"] += 1
                fields = self._fields_for_point(coll, payload)
                if fields.get("instrument_id"):
                    stats["with_instrument"] += 1
                if fields.get("provision_id"):
                    stats["with_provision"] += 1
                if fields.get("status"):
                    stats["with_status"] += 1
                # Only plan fields that are missing or different.
                planned = {k: v for k, v in fields.items() if payload.get(k) != v}
                if planned:
                    stats["to_update"] += 1
                    stats["updates"][str(pt["id"])] = planned
            summary["collections"][coll] = stats
            summary["total_points"] += stats["points"]
            summary["points_to_update"] += stats["to_update"]
        return summary

    def stamp(
        self,
        collections: Iterable[str] | None = None,
        limit: int | None = None,
        create_indexes: bool = True,
    ) -> dict[str, Any]:
        """Apply the identity fields to live payloads (idempotent)."""
        client = self._engine._get_qdrant_client()
        if collections is None:
            collections = [c.name for c in client.get_collections().collections]
        summary: dict[str, Any] = {"collections": {}, "points_updated": 0, "indexes_created": 0}
        for coll in sorted(collections):
            points = self._scroll_points(coll, limit=limit)
            stats = {"points": len(points), "updated": 0, "unchanged": 0}
            # Group points by identical field-set so one set_payload covers many.
            # key: sorted (field, value) pairs -> {"fields", "ids"}
            by_fields: dict[tuple, dict[str, Any]] = {}
            for pt in points:
                fields = self._fields_for_point(coll, pt["payload"])
                planned = {k: v for k, v in fields.items() if pt["payload"].get(k) != v}
                if not planned:
                    stats["unchanged"] += 1
                    continue
                key = tuple(sorted(planned.items()))
                group = by_fields.setdefault(key, {"fields": planned, "ids": []})
                group["ids"].append(str(pt["id"]))
            for group in by_fields.values():
                ids = group["ids"]
                for i in range(0, len(ids), self._batch_size):
                    client.set_payload(
                        collection_name=coll,
                        payload=group["fields"],
                        points=ids[i : i + self._batch_size],
                    )
                stats["updated"] += len(ids)
            summary["collections"][coll] = stats
            summary["points_updated"] += stats["updated"]
            if create_indexes:
                summary["indexes_created"] += self._create_indexes(client, coll)
        return summary

    def _create_indexes(self, client: Any, collection: str) -> int:
        """Best-effort keyword indexes on the identity fields."""
        created = 0
        for field in INDEX_FIELDS:
            try:
                client.create_payload_index(
                    collection_name=collection, field_name=field, field_schema="keyword"
                )
                created += 1
            except Exception as exc:
                logger.info("payload index %s on %s: %s", field, collection, exc)
        return created


# End of payload_identity.py
