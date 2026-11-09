"""Provision-stamp matching — one home for it (used by S1 diagnosis + analysis).

The benchmark's ``gold_source_chunks`` carry canonical instrument:section
stamps (``fssai:s16(1)``) while Qdrant payloads carry ``act_name`` +
``section_number`` / ``clause_number``. To score gold recall against a
retrieved pool, both must meet here; the mapping below is compiled from the
collections census (act_name values observed in each collection,
2026-11-05).
"""

from __future__ import annotations

import re
from typing import Any

#: gold stamp instrument prefix -> (collection, act_name regex)
GOLD_INDEX: dict[str, tuple[str | None, re.Pattern[str] | None]] = {
    "fssai": ("fssai_legal_768", re.compile(r"Food Safety", re.I)),
    "water_act": ("env_legal_768", re.compile(r"Water \(Prevention", re.I)),
    "air_act": ("env_legal_768", re.compile(r"Air \(Prevention", re.I)),
    "epa": ("env_legal_768", re.compile(r"Environment \(Protection", re.I)),
    "pwm_rules": (None, None),  # topical tag — no exact stamp
    "pwm_amendment_rules_2022_aug": (None, None),
    "pwm_amendment_rules_2022_jul": (None, None),
    "swm_rules": (None, None),
    "kmc": ("wb_state_legal_768", re.compile(r"Kolkata Municipal", re.I)),
    "wbpt": ("wb_state_legal_768", re.compile(r"Premises Tenancy", re.I)),
    "wbmo": ("wb_state_legal_768", re.compile(r".*", re.I)),
    "bda": ("wb_state_legal_768", re.compile(r".*", re.I)),
    "wb_infectious": ("animal_legal_768", re.compile(r"Infectious", re.I)),
    "sog": ("animal_legal_768", re.compile(r"Slaught|Cattle", re.I)),
    "livestock_quarantine": (None, None),
    "contract": ("commercial_legal_768", re.compile(r"Contract Act", re.I)),
    "limitation": ("commercial_legal_768", re.compile(r"Limitation Act", re.I)),
    "partnership": ("commercial_legal_768", re.compile(r"Partnership Act", re.I)),
    "comp": ("commercial_legal_768", re.compile(r"Companies Act", re.I)),
    "cpa": ("commercial_legal_768", re.compile(r"Consumer Protection", re.I)),
    "srf": ("commercial_legal_768", re.compile(r"Specific Relief", re.I)),
    "pcra": (None, None),
}

#: ``instrument:s16(1)``-style exact-section stamp (has a numeric section).
SECTION_STAMP = re.compile(r"^[a-z_0-9]+:s(\d+)", re.I)


def stamp_is_exact(stamp: str) -> bool:
    """True when *stamp* names a numeric Act/regulation section."""
    return bool(SECTION_STAMP.match(stamp))


def _norm(v: Any) -> str:
    return str(v or "")


def stamp_match(stamp: str, chunk: dict[str, Any]) -> bool:
    """True when *chunk* carries the provision the exact stamp names.

    ``chunk`` may be a pipeline result dict (``section_number`` etc.) or the
    compact ``chunk_stamps`` record schema (``sec`` / ``clause`` / ``act``).
    """
    m = SECTION_STAMP.match(stamp)
    if not m:
        return False
    prefix, _, _ = stamp.partition(":")
    coll, act_re = GOLD_INDEX.get(prefix, (None, None))
    if act_re is None:
        return False
    act = _norm(chunk.get("act_name", chunk.get("act")))
    if not act_re.search(act):
        return False
    sec = _norm(chunk.get("section_number", chunk.get("sec")))
    if sec != m.group(1):
        return False
    subsec = stamp.split("s" + m.group(1), 1)[1]
    if not subsec:
        return True
    clause = _norm(chunk.get("clause_number", chunk.get("clause")))
    if subsec in clause:
        return True
    # Act chunks often carry the section stamp but not a subsection stamp; the
    # subsection marker is then only visible in the text ("(1) The Food
    # Authority shall ...").  Fall back to the chunk head so a genuinely
    # retrieved subsection is not scored as a miss.
    text = _norm(chunk.get("text"))
    return subsec in text[:600]


__all__ = ["GOLD_INDEX", "SECTION_STAMP", "stamp_is_exact", "stamp_match"]
