"""Vector-store backup & restore (local archive of the production Qdrant index).

Backs up a Qdrant collection — chunk payloads PLUS dense and BM25 sparse
vectors — to a portable local JSON archive, and restores that archive into a
(recreated) collection.  This is a data-level export (``scroll`` with
``with_vectors=True``), the documented fallback when Qdrant Cloud's native
snapshot UI/API is unavailable; it preserves every point exactly as stored,
so it doubles as a local offline copy of the production embeddings.

Archive format (single JSON file)::

    {
      "collection": "fssai_legal_768",
      "exported_at": "2026-08-09T...",
      "vector_size": 768,
      "has_sparse": true,
      "point_count": 12804,
      "sha256": "<hex digest of the points payload>",
      "points": [
        {"id": "<uuid>", "vector": {"dense": [...], "text_sparse": {"indices": [...], "values": [...]}}, "payload": {...}},
        ...
      ]
    }

``vector`` mirrors the store's scroll output: a flat list for dense-only
collections, or a ``{dense, text_sparse}`` dict for hybrid collections
(sparse normalized to ``{indices, values}`` lists so the archive is plain
JSON).  The ``sha256`` covers the serialized ``points`` array so restore can
detect a corrupted/truncated archive.

Restore is idempotent-friendly: it recreates the collection with the same
sparse configuration as the archive (when missing), then upserts points in
batches.  Pass ``drop_existing=True`` to rebuild the target collection from
scratch (required when switching an existing collection's sparse layout).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.rag.qdrant_client import Point, QdrantStore

logger = logging.getLogger(__name__)

#: Default scroll page size for export.
EXPORT_BATCH_SIZE = 1000
#: Default upsert batch size for restore.
RESTORE_BATCH_SIZE = 100


def _normalize_vector(vector: Any) -> Any:
    """Make a scroll ``vector`` value plain-JSON serializable.

    Real client records return ``models.SparseVector`` objects for named
    sparse vectors (pydantic, not JSON-safe) — normalize to
    ``{indices, values}`` lists.  Dense vectors may also be numpy arrays.
    """
    if isinstance(vector, dict):
        return {k: _normalize_vector(v) for k, v in vector.items()}
    if hasattr(vector, "indices") and hasattr(vector, "values"):
        return {
            "indices": [int(i) for i in vector.indices],
            "values": [float(v) for v in vector.values],
        }
    if hasattr(vector, "tolist"):
        return vector.tolist()
    return vector


def _archive_sha(points: list[dict[str, Any]]) -> str:
    """SHA-256 of the canonical JSON serialization of the points array."""
    payload = json.dumps(points, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def backup_collection(
    store: QdrantStore,
    output_path: str,
    batch_size: int = EXPORT_BATCH_SIZE,
) -> dict[str, Any]:
    """Export a Qdrant collection (payloads + vectors) to a JSON archive.

    Args:
        store: The QdrantStore wrapping the collection to back up.
        output_path: Local archive path (``.json``).
        batch_size: Scroll page size.

    Returns:
        Summary dict (``path``, ``point_count``, ``vector_size``,
        ``has_sparse``, ``sha256``, ``latency_ms``).

    Raises:
        RuntimeError: When Qdrant is unavailable or the export yields no
            points (likely wrong collection name).
    """
    start = time.monotonic()
    client = store._get_client()
    if client is None:
        raise RuntimeError("Qdrant is unavailable (client/config) — cannot back up.")

    has_sparse = store.has_sparse_vectors()
    points = store.scroll_all(with_vectors=True, batch_size=batch_size)
    for item in points:
        if item.get("vector") is not None:
            item["vector"] = _normalize_vector(item["vector"])

    if not points:
        raise RuntimeError(
            f"collection {store.collection_name!r} has no points — refusing to write an "
            "empty archive (check RAG_QDRANT_COLLECTION)."
        )

    archive: dict[str, Any] = {
        "collection": store.collection_name,
        "exported_at": datetime.now(UTC).isoformat(),
        "vector_size": store.vector_size,
        "has_sparse": has_sparse,
        "point_count": len(points),
        "sha256": _archive_sha(points),
        "points": points,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(archive, fh, ensure_ascii=False)

    return {
        "path": output_path,
        "collection": store.collection_name,
        "point_count": len(points),
        "vector_size": store.vector_size,
        "has_sparse": has_sparse,
        "sha256": archive["sha256"],
        "latency_ms": int((time.monotonic() - start) * 1000),
    }


def load_archive(archive_path: str) -> dict[str, Any]:
    """Read and validate a backup archive (structure + SHA-256 integrity)."""
    with open(archive_path, encoding="utf-8") as fh:
        archive = json.load(fh)

    points = archive.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError(f"archive {archive_path!r} contains no points")
    expected = archive.get("sha256")
    if expected:
        actual = _archive_sha(points)
        if actual != expected:
            raise ValueError(
                f"archive {archive_path!r} failed integrity check (sha256 mismatch — "
                f"corrupted or truncated file)"
            )
    return archive


def restore_collection(
    store: QdrantStore,
    archive_path: str,
    drop_existing: bool = False,
    batch_size: int = RESTORE_BATCH_SIZE,
    create_payload_indexes: bool = True,
) -> dict[str, Any]:
    """Restore a backup archive into a Qdrant collection.

    Args:
        store: QdrantStore targeting the destination collection.  When its
            collection name differs from the archive's, pass a store built
            with the desired name.
        archive_path: Local archive JSON produced by :func:`backup_collection`.
        drop_existing: Recreate the collection (deletes existing points)
            before restoring — REQUIRED when the target collection's sparse
            layout differs from the archive's.
        batch_size: Upsert batch size.
        create_payload_indexes: Recreate §5.1 payload indexes on a new
            collection.

    Returns:
        Summary dict (``point_count``, ``points_restored``, ``has_sparse``,
        ``errors``, ``latency_ms``).

    Raises:
        ValueError: For a corrupt/empty archive or an id/vector shape error.
    """
    start = time.monotonic()
    archive = load_archive(archive_path)
    points = archive["points"]
    has_sparse = bool(archive.get("has_sparse"))

    if drop_existing:
        client = store._get_client()
        if client is None:
            logger.warning(
                "restore: drop_existing requested but Qdrant is unavailable — "
                "continuing (restore will fail at collection creation if unconfigured)."
            )
        else:
            try:
                client.delete_collection(store.collection_name)
            except Exception as exc:
                logger.warning("restore: drop existing collection failed: %s", exc)

    # Recreate with the same sparse layout as the archive (no-op if the
    # collection already exists and matches).
    store.ensure_collection(
        create_payload_indexes=create_payload_indexes,
        sparse_enabled=has_sparse,
    )

    structs: list[Point] = []
    errors: list[str] = []
    for item in points:
        point_id = str(item.get("id"))
        vector = item.get("vector")
        if vector is None:
            errors.append(f"point {point_id} has no vector — skipped")
            continue
        if isinstance(vector, dict):
            if "dense" not in vector:
                errors.append(
                    f"point {point_id} has a named-vector dict without a 'dense' "
                    f"entry (keys={sorted(vector)}) — skipped"
                )
                continue
            sparse = vector.get("text_sparse")
            structs.append(
                Point(
                    id=point_id,
                    vector=[float(v) for v in vector["dense"]],
                    sparse_vector=dict(sparse) if sparse else None,
                    payload=dict(item.get("payload") or {}),
                )
            )
        else:
            structs.append(
                Point(id=point_id, vector=[float(v) for v in vector], payload=dict(item.get("payload") or {}))
            )

    restored = 0
    for i in range(0, len(structs), batch_size):
        chunk = structs[i : i + batch_size]
        try:
            restored += store.upsert_points(chunk)
        except Exception as exc:
            errors.append(f"batch {i // batch_size} upsert failed: {exc}")

    return {
        "collection": store.collection_name,
        "archive_point_count": len(points),
        "points_restored": restored,
        "has_sparse": has_sparse,
        "errors": errors,
        "latency_ms": int((time.monotonic() - start) * 1000),
    }


# End of backup.py
