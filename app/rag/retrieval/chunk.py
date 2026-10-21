"""Chunk field access seam — one object/dict policy for retrieved chunks.

Retrieval stages consume two shapes interchangeably:

* :class:`~app.rag.retrieval.result.RetrievedChunk` (and other chunk-like
  objects), and
* the serialized payload dict produced by ``to_dict()`` / Qdrant scroll.

Before this module every stage re-implemented the ``isinstance(chunk, dict)``
branch, and the copies drifted on absent values (``None`` vs ``""``) and score
coercion.  These helpers are the single policy:

* :func:`field` — dict ``.get`` first, otherwise ``getattr``; *default* wins
  only when the field is absent/``None`` for dicts (``get`` semantics).
* :func:`text` — always a ``str``, empty when absent.
* :func:`chunk_id` — ``str`` when present, otherwise *default* (``None`` when
  omitted, ``""`` for callers that need a string key).
* :func:`score` — ``float``, ``0.0`` for absent or non-numeric values.
* :func:`set_field` / :func:`set_score` — dict assignment or attribute write.

No behaviour is attached to a chunk class: ``RetrievedChunk``'s interface is
unchanged, so payloads and dataclasses remain interchangeable at every seam.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "chunk_id",
    "field",
    "score",
    "set_field",
    "set_score",
    "text",
]


def field(chunk: Any, name: str, default: Any = None) -> Any:
    """Read *name* from a payload dict or a chunk-like object."""
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


def text(chunk: Any, default: str = "") -> str:
    """Chunk text, normalized to ``str`` (empty when absent)."""
    return str(field(chunk, "text", default) or default)


def chunk_id(chunk: Any, default: str | None = None) -> str | None:
    """Chunk id as ``str``; *default* when the field is missing."""
    value = field(chunk, "chunk_id", None)
    if value is None:
        return default
    return str(value)


def score(chunk: Any, default: float = 0.0) -> float:
    """Chunk score as ``float``; *default* for missing or malformed values."""
    try:
        return float(field(chunk, "score", default) or default)
    except (TypeError, ValueError):
        return float(default)


def set_field(chunk: Any, name: str, value: Any) -> None:
    """Write *name* onto a payload dict or a chunk-like object."""
    if isinstance(chunk, dict):
        chunk[name] = value
    else:
        setattr(chunk, name, value)


def set_score(chunk: Any, value: float) -> None:
    """Write a numeric score; immutable chunks keep their original score."""
    try:
        set_field(chunk, "score", float(value))
    except AttributeError:
        # Mirrors the old reranker helper: a frozen/slotted chunk is ordered
        # by the computed score but cannot store it.
        pass
