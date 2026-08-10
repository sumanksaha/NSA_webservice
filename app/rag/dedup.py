"""SHA-256 content-hash deduplication (Agent A, Phase 1 — Day 5, §4).

Reuses the SHA-256 pattern from ``app/services/version_control.py`` /
``app/services/audit.py`` (R0) to fingerprint documents and chunks so a
corpus rebuild never embeds or upserts content that is already indexed.

Design:

- :class:`ContentHasher` — stable SHA-256 hexdigest of *normalized* text
  (whitespace collapsed) so cosmetic formatting differences do not defeat
  dedup.
- :class:`SeenHashStore` protocol + :class:`MemoryHashStore` — where seen
  hashes live.  ``MemoryHashStore`` is the default (in-process); production
  wiring can back it with the ``LegalDocument.file_hash`` unique column /
  the Qdrant ``content_hash`` payload field once the Phase 3 models land.
- :class:`ChunkDeduper` — document-level ``is_duplicate_document`` and
  chunk-level ``filter_new`` / ``record`` with payload ``content_hash``
  stamping.

All stores are injectable so the module is fully testable without a
database or Qdrant.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def normalize_for_hash(text: str) -> str:
    """Collapse all whitespace runs to single spaces (stable fingerprinting)."""
    return " ".join((text or "").split())


class ContentHasher:
    """SHA-256 fingerprinting for document/chunk text (§4 Day 5)."""

    @staticmethod
    def compute(text: str) -> str:
        """Return the SHA-256 hexdigest of ``text`` (normalized).

        Mirrors the SHA-256 pattern used by ``app/services/audit.py``
        (``compute_hash``) and ``app/services/version_control.py``.
        """
        return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


class SeenHashStore(Protocol):
    """Protocol for the dedup hash registry."""

    def contains(self, content_hash: str) -> bool: ...

    def add(self, content_hash: str) -> None: ...

    def add_many(self, content_hashes: list[str]) -> None: ...


class MemoryHashStore:
    """In-memory hash registry — the default (and test) implementation.

    Production wiring should replace this with a persistent store (e.g. the
    ``LegalDocument.file_hash`` unique column) so dedup survives restarts.
    """

    def __init__(self, initial: set[str] | None = None) -> None:
        self._seen: set[str] = set(initial or ())

    def contains(self, content_hash: str) -> bool:
        return content_hash in self._seen

    def add(self, content_hash: str) -> None:
        self._seen.add(content_hash)

    def add_many(self, content_hashes: list[str]) -> None:
        self._seen.update(content_hashes)

    def __len__(self) -> int:
        return len(self._seen)


class ChunkDeduper:
    """Document- and chunk-level dedup for the ingestion pipeline.

    Args:
        hasher: Optional :class:`ContentHasher` (injected for tests).
        store: Optional :class:`SeenHashStore` (default in-memory).
    """

    def __init__(
        self,
        hasher: type[ContentHasher] | None = None,
        store: SeenHashStore | None = None,
    ) -> None:
        self._hasher = hasher or ContentHasher
        self._store = store or MemoryHashStore()

    @property
    def store(self) -> SeenHashStore:
        """The backing hash registry (exposed for diagnostics/tests)."""
        return self._store

    # ------------------------------------------------------------------ #
    # Document-level dedup
    # ------------------------------------------------------------------ #

    def document_hash(self, text: str) -> str:
        """SHA-256 fingerprint of a full (cleaned) document text."""
        return self._hasher.compute(text)

    def is_duplicate_document(self, text: str) -> bool:
        """Whether this document text has already been indexed."""
        return self._store.contains(self.document_hash(text))

    # ------------------------------------------------------------------ #
    # Chunk-level dedup
    # ------------------------------------------------------------------ #

    def chunk_hash(self, chunk: Any) -> str:
        """SHA-256 fingerprint of a single chunk's text."""
        return self._hasher.compute(getattr(chunk, "chunk_text", ""))

    def stamp_chunk(self, chunk: Any) -> str:
        """Compute and stamp ``content_hash`` on a chunk, returning it."""
        content_hash = self.chunk_hash(chunk)
        if hasattr(chunk, "content_hash"):
            chunk.content_hash = content_hash
        return content_hash

    def filter_new(self, chunks: list[Any]) -> tuple[list[Any], list[str]]:
        """Split ``chunks`` into ``(new_chunks, duplicate_hashes)``.

        New chunks are stamped with their ``content_hash``; already-seen
        chunks are returned (by hash) in ``duplicate_hashes`` and skipped.
        """
        new_chunks: list[Any] = []
        duplicate_hashes: list[str] = []
        for chunk in chunks:
            content_hash = self.stamp_chunk(chunk)
            if self._store.contains(content_hash):
                duplicate_hashes.append(content_hash)
            else:
                new_chunks.append(chunk)
        return new_chunks, duplicate_hashes

    def record(self, chunks: list[Any] | None = None, content_hashes: list[str] | None = None) -> None:
        """Mark hashes as seen (after a successful upsert)."""
        if chunks:
            self._store.add_many([self.chunk_hash(c) for c in chunks])
        if content_hashes:
            self._store.add_many(list(content_hashes))


# End of dedup.py
