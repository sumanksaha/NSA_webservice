"""Thin adapter over the native ``nsa_rust`` fuzzy helpers.

Single source of truth for the "Rust acceleration or pure-Python fallback"
dispatch that was previously duplicated in two places (AGENTS.md Candidate 2):

* ``app/search/indexer.py`` — an inline ``try/except`` block importing
  ``nsa_rust.field_score`` / ``highlight_text`` / ``snippet_around_matches``
  plus two ``_maybe_*`` dispatcher functions and an inline try/except inside
  ``_highlight_title``.
* ``app/rag/retrieval/sparse_retriever.py`` — its own ``try/except`` block
  importing ``nsa_rust.field_score`` and a ``SparseRetriever._field_score``
  staticmethod that re-implemented the same dispatch.

The pure-Python fallbacks (``_field_score``, ``_snippet_around_matches``,
``_highlight_text``) remain defined in ``app.search.indexer`` — they are
imported *lazily* inside each fallback branch so the
``indexer -> rust_fuzzy -> indexer`` edge never materialises at import time
(the dispatchers are only invoked at runtime, by which point ``indexer`` is
fully loaded).

Byte-exact parity contract
--------------------------
Each ``nsa_rust`` function guards empty ``text`` identically to its Python
counterpart (``text.is_empty()`` -> ``0.0`` for ``field_score``; -> ``""``
for the snippet/highlight helpers — see ``rust/src/search_fuzzy.rs`` lines
580, 492, 594).  So the dispatchers here are behaviour-preserving for every
existing call site, whether or not the extension is compiled, and the
per-call ``except Exception`` fallback matches the originals.

When ``nsa_rust`` is unavailable (this dev host has no C linker to build the
PyO3 extension — see task.md), the same pure-Python code paths run; on any
host with a C toolchain ``maturin develop --release`` in ``rust/`` installs the
extension, activating the native fast path with zero caller changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Probe once at import time.  A missing extension is an ordinary, supported
# state (the pure-Python fallbacks are used); a present-but-broken one is
# caught per-call in each dispatcher below.
try:
    import nsa_rust as _nsa_rust
except ImportError:  # pragma: no cover - depends on the build environment
    _nsa_rust = None

#: True when the native ``nsa_rust`` extension is importable.  Exposed for
#: diagnostics / settings introspection; callers should branch on the
#: dispatchers below, not on this flag.
RUST_AVAILABLE: bool = _nsa_rust is not None


def _maybe_field_score(query: str, text: str) -> float:
    """Best fuzzy similarity of ``query`` against ``text`` (0–100).

    Delegates to ``nsa_rust.field_score`` when the extension is available,
    otherwise to ``app.search.indexer._field_score`` (rapidfuzz-backed).
    Empty ``text`` short-circuits to ``0.0`` — matching both original
    implementations (Rust guards ``text.is_empty()``; Python's ``_field_score``
    guards ``not text``), so this is byte-exact for every call site.
    """
    if not text:
        return 0.0
    if _nsa_rust is not None:
        try:
            return _nsa_rust.field_score(query, text)
        except Exception:  # pragma: no cover - defensive: rust shouldn't raise
            logger.debug("nsa_rust.field_score failed; falling back", exc_info=True)
    from app.search.indexer import _field_score  # lazy: breaks import cycle

    return _field_score(query, text)


def _maybe_snippet_around_matches(
    query: str,
    text: str,
    width: int = 80,
    fuzzy_word_threshold: float = 60.0,
) -> str:
    """Word-bounded ``<mark>``-highlighted snippet for ``query``.

    Delegates to ``nsa_rust.snippet_around_matches`` when available, otherwise
    to ``app.search.indexer._snippet_around_matches``.  Mirrors the original
    ``indexer._maybe_snippet_around_matches`` dispatch exactly.
    """
    if _nsa_rust is not None:
        try:
            return _nsa_rust.snippet_around_matches(query, text, width, fuzzy_word_threshold)
        except Exception:  # pragma: no cover - defensive
            logger.debug(
                "nsa_rust.snippet_around_matches failed; falling back",
                exc_info=True,
            )
    from app.search.indexer import _snippet_around_matches  # lazy: cycle break

    return _snippet_around_matches(query, text, width, fuzzy_word_threshold)


def _maybe_highlight_text(
    query: str,
    text: str,
    fuzzy_word_threshold: float = 60.0,
) -> str:
    """Return ``text`` with matched terms wrapped in ``<mark>`` tags.

    Delegates to ``nsa_rust.highlight_text`` when available, otherwise to
    ``app.search.indexer._highlight_text``.  Replaces the inline
    try/except block that previously lived in ``indexer._highlight_title``
    (which always passed the hard-coded ``60.0`` threshold).
    """
    if _nsa_rust is not None:
        try:
            return _nsa_rust.highlight_text(query, text, fuzzy_word_threshold)
        except Exception:  # pragma: no cover - defensive
            logger.debug("nsa_rust.highlight_text failed; falling back", exc_info=True)
    from app.search.indexer import _highlight_text  # lazy: breaks import cycle

    return _highlight_text(query, text, fuzzy_word_threshold)
