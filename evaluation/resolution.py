"""Gold ↔ chunk resolution layer.

The frozen benchmark addresses provisions with ids like ``fssai:s16(1)``
while Qdrant payloads carry ``act_name`` / ``section_number`` and Neo4j uses
ids like ``FSS_..._SEC_1``.  This module maps between those namespaces:

* :func:`build_payload_index`   — scrolls every Qdrant collection once and
  caches ``{point_id: payload}``.
* :class:`FamilyMap`            — derived from the gold registry: maps the
  benchmark family prefix (``fssai``, ``wbmo``, …) to the full instrument
  names that family covers.
* :func:`payload_to_keys`       — derives candidate ``(family, section)``
  keys from a chunk payload so a retrieved chunk can be matched to gold.
* :func:`gold_in_corpus`        — resolves each gold unit to payload points
  (corpus-coverage check for the failure decomposition).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from evaluation.benchmark import GoldUnit, load_gold_registry
from evaluation.config import CACHE_DIR, RAW_DIR

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_ACT_STOP = re.compile(r"^(the|an|a)\s+")


def norm_act_name(name: str | None) -> str:
    """Lower-case, strip leading articles + punctuation for fuzzy act matching."""
    if not name:
        return ""
    s = _PUNCT_RE.sub(" ", name.lower()).strip()
    return _ACT_STOP.sub("", s)


def norm_section(value: Any) -> str | None:
    """Base section number of any section-ish string ('16(2)(ii)' -> '16')."""
    if value is None:
        return None
    m = re.match(r"\s*(\d{1,4})", str(value).strip())
    return m.group(1) if m else None


#: Distinctive-substring aliases for families whose corpus title differs
#: from the gold registry's act name (e.g. PCA *Rules 2017* text embedded
#: under the PCA *Act 1960* title; WB Meat Order stamped under the Essential
#: Commodities Act title).  Most-specific substrings first.
_FAMILY_ALIASES: dict[str, list[str]] = {
    "fssai": ["food safety and standards"],
    "epa": ["environment protection act"],
    "water_act": ["water prevention and control of pollution"],
    "air_act": ["air prevention and control of pollution"],
    "pwm_rules": ["plastic waste management rules 2016", "plastic waste management"],
    "pwm_amendment_rules_2022_jul": ["plastic waste management amendment rules 2022"],
    "pwm_amendment_rules_2022_aug": ["plastic waste management amendment rules 2022"],
    "swm_rules": ["solid waste management"],
    "kmc": ["kolkata municipal corporation"],
    "wbpt": ["west bengal premises tenancy"],
    "contract": ["indian contract act"],
    "sog": ["sale of goods act"],
    "partnership": ["indian partnership act"],
    "comp": ["companies act 2013"],
    "limitation": ["limitation act"],
    "cpa": ["consumer protection act"],
    "srf": ["specific relief act"],
    "pcra": ["prevention of cruelty to animals"],
    "bda": ["diseases of animals"],
    "wbmo": ["west bengal meat order", "meat order"],
    "wb_infectious": ["infectious and contagious diseases in animals", "infectious diseases in animals"],
    "livestock_quarantine": ["livestock import quarantine"],
    "bns": ["bharatiya nyaya sanhita"],
}


class FamilyMap:
    """Map benchmark family prefixes -> canonical instrument names + aliases.

    Built from the gold provision registry (exact act names) plus a curated
    alias table for instruments whose corpus title differs from the registry
    act name.  ``family_s_for_act`` returns *all* matching families so one
    chunk can cover gold units of several families.
    """

    def __init__(self) -> None:
        self.family_to_acts: dict[str, list[str]] = {}
        self.act_to_family: dict[str, str] = {}
        registry = load_gold_registry()
        for pid, rec in registry.items():
            family = str(pid).split(":", 1)[0]
            act = rec.get("act")
            if act:
                n = norm_act_name(act)
                if n and family not in self.act_to_family:
                    self.act_to_family[n] = family
                if act not in self.family_to_acts.setdefault(family, []):
                    self.family_to_acts[family].append(act)
        # alias list of (family, alias) pairs, longest substring first
        # (most specific wins)
        self.alias_list: list[tuple[str, str]] = sorted(
            ((fam, alias) for fam, aliases in _FAMILY_ALIASES.items() for alias in aliases),
            key=lambda pair: len(pair[1]),
            reverse=True,
        )

    @property
    def families(self) -> list[str]:
        return sorted(set(self.family_to_acts) | set(_FAMILY_ALIASES))

    def family_for_act(self, act_name: str | None) -> str | None:
        """Best single family (legacy helper — prefers most specific match)."""
        fams = self.family_s_for_act(act_name)
        return fams[0] if fams else None

    def family_s_for_act(self, act_name: str | None) -> list[str]:
        """All families a title could belong to (exact then alias containment)."""
        n = norm_act_name(act_name)
        if not n:
            return []
        found: list[str] = []
        seen: set[str] = set()
        if n in self.act_to_family:
            fam = self.act_to_family[n]
            found.append(fam)
            seen.add(fam)
        for family, alias in self.alias_list:
            if family in seen:
                continue
            if len(n) >= len(alias) and alias in n:
                found.append(family)
                seen.add(family)
        return found


# --------------------------------------------------------------------------- #
# Payload index
# --------------------------------------------------------------------------- #
_PAYLOAD_INDEX_CACHE = CACHE_DIR / "payload_index.jsonl"


def build_payload_index(store_factory: Any, collections: list[str], force: bool = False) -> dict[str, dict]:
    """Scroll all Qdrant collections once; cache + return ``{point_id: payload}``."""
    if _PAYLOAD_INDEX_CACHE.exists() and not force:
        index: dict[str, dict] = {}
        with open(_PAYLOAD_INDEX_CACHE, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                index[rec["id"]] = rec["payload"]
        logger.info("payload index loaded from cache: %d points", len(index))
        return index

    index = {}
    for coll in collections:
        store = store_factory(coll)
        logger.info("scrolling %s ...", coll)
        points = store.scroll_all(batch_size=500)
        for p in points:
            index[str(p["id"])] = p.get("payload") or {}
        logger.info("  %s -> %d points", coll, len(points))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PAYLOAD_INDEX_CACHE, "w", encoding="utf-8") as f:
        for pid, payload in index.items():
            f.write(json.dumps({"id": pid, "payload": payload}, ensure_ascii=False) + "\n")
    logger.info("payload index built: %d points", len(index))
    return index


def payload_to_keys(payload: dict, family_map: FamilyMap) -> list[tuple[str, str | None]]:
    """Derive all ``(family, section)`` keys from a chunk payload.

    V5 fix (2026-08-12, Task 5): consult BOTH ``act_name`` and
    ``document_title``, not ``act_name or document_title``.  The old either/or
    meant ``document_title`` was never consulted when ``act_name`` was set — a
    latent bug for sub-instrument documents whose payload ``act_name`` is the
    parent Act (e.g. the WB Meat Order corpus is stamped
    ``act_name="Essential Commodities Act, 1955"`` while the gold family is
    ``wbmo``, named only in ``document_title``).  Families are unioned with
    act_name preferred first (most specific signal), so a chunk may match
    several families (e.g. the PCA corpus stamped under the Act title covers
    the pcra family).  A zero-effect regression against V4 is recorded in
    ``evaluation/out/ceiling_v5/payload_to_keys_regression.json``.
    """
    section = norm_section(payload.get("section_number") or payload.get("subsection"))
    keys: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for source in (payload.get("act_name"), payload.get("document_title")):
        if not source:
            continue
        for family in family_map.family_s_for_act(str(source)):
            if family not in seen:
                seen.add(family)
                keys.append((family, section))
    return keys


def matches_gold(payload: dict, unit: GoldUnit, family_map: FamilyMap) -> bool:
    """Whether a chunk payload covers a gold unit.

    * Numeric gold sections require an equal normalised ``section_number``.
    * Instrument-level gold references (``section is None``) are covered by
      any chunk of the same family (e.g. ``fssai:regs/contaminants``).
    """
    keys = payload_to_keys(payload, family_map)
    for family, section in keys:
        if family != unit.family:
            continue
        if unit.section is None:
            return True
        if section is not None and section == unit.section:
            return True
    return False


def gold_in_corpus(
    units: list[GoldUnit],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> dict[str, Any]:
    """Resolve gold units to payload points (corpus-coverage check)."""
    result: dict[str, Any] = {"resolved_units": 0, "unresolved_units": 0, "unit_points": {}}
    for unit in units:
        pts = [
            pid
            for pid, payload in payload_index.items()
            if matches_gold(payload, unit, family_map)
        ]
        if pts:
            result["resolved_units"] += 1
            result["unit_points"][unit.provision_id] = pts
        else:
            result["unresolved_units"] += 1
            result["unit_points"][unit.provision_id] = []
    return result


def chunks_cover_gold(
    chunk_ids: list[str],
    gold_units: list[GoldUnit],
    payload_index: dict[str, dict],
    family_map: FamilyMap,
) -> dict[str, Any]:
    """Ranked per-unit hit info for a ranked chunk-id list.

    Returns per gold unit: ``{"hit": bool, "rank": int|None}`` (1-based rank
    of the first covering chunk) plus the matched chunk ids.
    """
    info: dict[str, dict[str, Any]] = {}
    matched_any: set[str] = set()
    for unit in gold_units:
        hit_rank = None
        for i, cid in enumerate(chunk_ids):
            payload = payload_index.get(cid)
            if payload is not None and matches_gold(payload, unit, family_map):
                hit_rank = i + 1
                matched_any.add(cid)
                break
        info[unit.provision_id] = {"hit": hit_rank is not None, "rank": hit_rank}
    return {"units": info, "matched_chunk_ids": sorted(matched_any)}
