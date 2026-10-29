"""Optional Redis cache for knowledge-graph node look-ups.

Nodes are cached under ``kg:node:{id}`` so frequently accessed statutes and
case entities can be served without a DB round-trip.  The cache is strictly
best-effort: when Redis is unavailable (or not configured) every operation is
a no-op and callers fall back to the database.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: TTL for cached node payloads (seconds).
NODE_TTL_SECONDS = 3600


class KGCache:
    """Thin wrapper around a Redis client for ``kg:node:{id}`` look-ups."""

    def __init__(self, redis_url: str | None = None, client: Any = None) -> None:
        self._client = client
        self._redis_url = redis_url

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        if self._redis_url is None:
            return None
        try:
            import redis  # type: ignore[import-not-found]

            self._client = redis.Redis.from_url(self._redis_url, decode_responses=True)
            # Prove connectivity (cheap; fails fast when Redis is down).
            self._client.ping()
            return self._client
        except Exception as exc:  # pragma: no cover - cache is best effort
            logger.debug("Redis unavailable for KG cache: %s", exc)
            self._client = None
            return None

    def get(self, node_id: str) -> dict | None:
        client = self._connect()
        if client is None:
            return None
        try:
            raw = client.get(f"kg:node:{node_id}")
        except Exception as exc:  # pragma: no cover - cache is best effort
            logger.debug("KG cache get failed for %s: %s", node_id, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    def set(self, node_id: str, value: Any, ttl: int = NODE_TTL_SECONDS) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            payload = value if isinstance(value, str) else json.dumps(value, default=str)
            client.setex(f"kg:node:{node_id}", ttl, payload)
        except Exception as exc:  # pragma: no cover - cache is best effort
            logger.debug("KG cache set failed for %s: %s", node_id, exc)

    def delete(self, node_id: str) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            client.delete(f"kg:node:{node_id}")
        except Exception as exc:  # pragma: no cover - cache is best effort
            logger.debug("KG cache delete failed for %s: %s", node_id, exc)
