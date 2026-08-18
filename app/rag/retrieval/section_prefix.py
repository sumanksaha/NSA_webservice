"""Section-prefix helper for CE train/serve parity (CV2 P1, 2026-08-18).

The CE-v2 improvement plan (§2.4 Step 2, G2) trains the cross-encoder with an
explicit legal-identity prefix baked into the passage text (``§<section>
<text>``) so the model does not have to infer legal hierarchy from raw text
alone.  This module is the **single shared seam**: the dataset builder
(``evaluation/pairwise_dataset --section-prefix``) and every serve-side path
(local CE, ensemble head, remote client) must apply *byte-identical* prefixes,
or the deployed model silently never receives the signal it was trained with.

Format
------
- Act chunks (``section_number`` present): ``§16 <text>``
- Regulation/rule chunks with no Act section but a dotted clause number: the
  clause is the honest identity (G7/G8), so the prefix falls back to it —
  ``§2.4.15 <text>``.  (Keeps the single ``§`` marker; the model learns the
  shape, and both cases are legal-identity signals.)
- No identity: passage returned unchanged (G4 fallback — never invent a
  prefix).

Flag
----
``RAG_CE_SECTION_PREFIX`` (default off) — Flask config wins when an app
context exists, else the env var.  Off = zero behavior change for v1 /
unprefixed models.
"""
from __future__ import annotations

import os
import re

_PREFIX_RE = re.compile(r"^\u00a7\S+\s+")


def ce_section_prefix_enabled() -> bool:
    """Whether the legal-identity prefix is active (``RAG_CE_SECTION_PREFIX``).

    Mirrors the ``_flag_enabled`` pattern used by RAG_KG_EXPANSION /
    RAG_KG_FUSION: Flask config wins inside an app context, else the env var.
    """
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("RAG_CE_SECTION_PREFIX", False))
    except Exception:
        pass
    return os.environ.get("RAG_CE_SECTION_PREFIX", "false").lower() == "true"


def prefix_passage(
    text: str,
    section_number: str | None = None,
    clause_number: str | None = None,
) -> str:
    """Prefix *text* with its legal identity when one is known.

    *section_number* wins over *clause_number* (Act identity is more
    specific).  Returns *text* unchanged when the prefix flag is off or no
    identity is available.  Idempotent — an already-prefixed passage is never
    double-prefixed.
    """
    if not ce_section_prefix_enabled():
        return text
    identity = _pick_identity(section_number, clause_number)
    if not identity:
        return text
    if _PREFIX_RE.match(text):
        return text
    return f"\u00a7{identity} {text}"


def _pick_identity(section_number: str | None, clause_number: str | None) -> str | None:
    """Return the strongest available identity, or ``None``."""
    sec = str(section_number or "").strip()
    if sec:
        # Normalise to the base section number (drop subsections — the prefix
        # signals the section, not the clause position within it).
        m = re.match(r"(\d{1,4})", sec)
        return m.group(1) if m else sec
    clause = str(clause_number or "").strip()
    return clause or None
