"""Domain -> Qdrant collection registry (Phase 1 — multi-domain RAG).

Each legal domain lives in its own collection so per-domain eval, enrichment
and rollback stay isolated (user-confirmed topology 2026-08-10).  The FSSAI
collection keeps the legacy ``fssai_legal_768`` name; the manifest in
``other domain/manifest.json`` carries the same default map.

Resolution order for :func:`collection_for_domain`:

1. A Flask config override ``RAG_QDRANT_COLLECTION_<DOMAIN_UPPER>`` (set from
   ``RAG_QDRANT_COLLECTION_<DOMAIN>`` env vars in ``app/__init__.py``).
2. The default map in :data:`DOMAIN_COLLECTIONS`.
3. The primary collection (``fssai_legal_768``) as a safe fallback.

``RAG_QDRANT_COLLECTION`` itself remains the app-wide default collection for
code paths that do not target a domain.
"""

from __future__ import annotations

from typing import Any

#: Domain -> default collection (mirrors the multi-domain manifest).
DOMAIN_COLLECTIONS: dict[str, str] = {
    "fssai": "fssai_legal_768",
    "env": "env_legal_768",
    "commercial": "commercial_legal_768",
    "animal": "animal_legal_768",
    "wb_state": "wb_state_legal_768",
    "criminal": "criminal_legal_768",
}

#: Accepted aliases mapping onto canonical domains.
_DOMAIN_ALIASES: dict[str, str] = {
    "food": "fssai",
    "environment": "env",
    "state": "wb_state",
    "municipal": "wb_state",
    "penal": "criminal",
}

DEFAULT_DOMAIN = "fssai"
DEFAULT_COLLECTION = "fssai_legal_768"


def collection_for_domain(domain: str | None, config: dict[str, Any] | None = None) -> str:
    """Resolve the Qdrant collection name for a domain.

    Args:
        domain: Domain key (e.g. ``"env"``, ``"commercial"``, ``"animal"``,
            ``"wb_state"``, ``"fssai"``); ``None``/unknown -> primary.
        config: Optional Flask config (or any ``Mapping``) consulted for a
            ``RAG_QDRANT_COLLECTION_<DOMAIN_UPPER>`` override.

    Returns:
        The collection name.
    """
    key = str(domain or "").strip().lower()
    canonical = _DOMAIN_ALIASES.get(key, key)
    if config is not None:
        try:
            override = config.get(f"RAG_QDRANT_COLLECTION_{canonical.upper()}")
        except Exception:  # noqa: BLE001 - arbitrary Mapping may not have .get
            override = None
        if override:
            return str(override)
    return DOMAIN_COLLECTIONS.get(canonical, DEFAULT_COLLECTION)


# End of collections.py
