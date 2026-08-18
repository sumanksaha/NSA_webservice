"""Benchmark loading + schema validation (protocol §2).

Loads the frozen v1.0 benchmark artifacts, reports which gold signals are
available per question, and builds per-question "gold units" used by every
metric.

Gold-signal report (protocol §2):
    AVAILABLE GOLD SIGNAL   — fields present on the frozen questions
    MISSING GOLD SIGNAL     — fields the protocol lists but the benchmark
                              does not carry (reported, never invented)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from evaluation.config import (
    BENCHMARK_FILE,
    GOLD_PROVISIONS_FILE,
    GOLD_SOURCES_FILE,
)

#: Fields the protocol (§2) would like per question; mapped to the JSONL keys.
_PROTOCOL_FIELDS: dict[str, str] = {
    "question_id": "question_id",
    "question_text": "question",
    "domain": "domains",
    "legal_instrument": "collections",  # instrument family lives in gold registry
    "expected_provision": "primary_provisions",
    "expected_source": "gold_source_chunks",
    "jurisdiction": "jurisdiction",
    "temporal_status": "temporal_constraints",
    "question_type": "question_type",
    "difficulty": "difficulty",
    "cross_domain_requirement": "domains",
    "expected_answer": "acceptable_conclusion",
    "gold_evidence": "gold_source_chunks",
    "gold_citation": "gold_document_ids",
    "abstention_requirement": "insufficient_evidence",
}


@dataclass
class GoldUnit:
    """A single gold provision referenced by a question."""

    provision_id: str          # benchmark scheme, e.g. "fssai:s16(1)"
    family: str                # id prefix, e.g. "fssai"
    section: str | None        # registry section number (None for whole-instrument refs)
    act: str                   # full act name from the registry
    collection: str | None     # qdrant collection from the registry
    document_id: str | None    # registry document id
    gain: float                # 2 primary / 1 acceptable / 0 supporting
    role: str                  # "primary" | "acceptable" | "supporting"


@dataclass
class BenchmarkQuestion:
    """One frozen benchmark question + its resolved gold units."""

    raw: dict[str, Any]
    gold_units: list[GoldUnit] = field(default_factory=list)

    @property
    def question_id(self) -> str:
        return self.raw["question_id"]

    @property
    def question(self) -> str:
        return self.raw["question"]

    @property
    def domains(self) -> list[str]:
        return list(self.raw.get("domains", []))

    @property
    def collections(self) -> list[str]:
        return list(self.raw.get("collections", []))

    @property
    def question_types(self) -> list[str]:
        return list(self.raw.get("question_type", []))

    @property
    def difficulty(self) -> str:
        return self.raw.get("difficulty", "")

    @property
    def split(self) -> str:
        return self.raw.get("split", "")

    @property
    def insufficient_evidence(self) -> bool:
        return bool(self.raw.get("insufficient_evidence"))

    @property
    def jurisdiction(self) -> str:
        return self.raw.get("jurisdiction", "")

    @property
    def temporal_constraints(self) -> list[Any]:
        return list(self.raw.get("temporal_constraints", []))

    @property
    def gold_authorities(self) -> list[str]:
        return list(self.raw.get("gold_authorities", []))

    @property
    def acceptable_conclusion(self) -> str:
        return self.raw.get("acceptable_conclusion", "")

    @property
    def gold_concepts(self) -> list[str]:
        return list(self.raw.get("gold_concepts", []))

    @property
    def gold_document_ids(self) -> list[str]:
        return list(self.raw.get("gold_document_ids", []))

    @property
    def gold_source_chunks(self) -> list[str]:
        return list(self.raw.get("gold_source_chunks", []))

    def primary_units(self) -> list[GoldUnit]:
        return [u for u in self.gold_units if u.role == "primary"]

    def relevant_units(self) -> list[GoldUnit]:
        """Primary + acceptable (the rubric's relevance set for nDCG)."""
        return [u for u in self.gold_units if u.role in ("primary", "acceptable")]

    def recall_units(self) -> list[GoldUnit]:
        """Everything gold — used for the broad Recall@K (gold_source_chunks)."""
        return list(self.gold_units)


def _load_json(path: Any) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_gold_registry() -> dict[str, dict[str, Any]]:
    """Load the 97-record gold provision registry keyed by provision id."""
    data = _load_json(GOLD_PROVISIONS_FILE)
    records: dict[str, dict[str, Any]] = {}
    for rec in data.get("provisions", []):
        pid = rec.get("id")
        if pid:
            records[pid] = rec
    return records


def load_gold_sources() -> dict[str, Any]:
    """Load the canonical source-document registry (22 documents)."""
    return _load_json(GOLD_SOURCES_FILE)


def load_questions() -> list[BenchmarkQuestion]:
    """Load the 150 frozen questions and resolve gold units against the registry."""
    registry = load_gold_registry()
    questions: list[BenchmarkQuestion] = []
    with open(BENCHMARK_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            q = BenchmarkQuestion(raw=raw)
            q.gold_units = _resolve_units(raw, registry)
            questions.append(q)
    return questions


def _resolve_units(raw: dict[str, Any], registry: dict[str, dict[str, Any]]) -> list[GoldUnit]:
    """Map a question's provision references to registry-backed GoldUnits.

    A reference may be a bare registry id (``fssai:s16(1)``) or an aliased
    reference (``fssai:regs/licensing``) whose registry record carries the
    canonical act/section.  References missing from the registry are still
    kept (family parsed from the id) and flagged via ``document_id=None`` —
    nothing is silently dropped.
    """
    units: list[GoldUnit] = []
    seen: set[str] = set()

    def add(provision_id: str, role: str, gain: float) -> None:
        if provision_id in seen:
            return
        seen.add(provision_id)
        rec = registry.get(provision_id, {})
        family = str(provision_id).split(":", 1)[0]
        units.append(
            GoldUnit(
                provision_id=provision_id,
                family=family,
                section=_norm_section(rec.get("section") or _section_from_id(provision_id)),
                act=rec.get("act") or "",
                collection=rec.get("collection"),
                document_id=rec.get("document_id"),
                gain=gain,
                role=role,
            )
        )

    for pid in raw.get("primary_provisions", []):
        add(pid, "primary", GAIN_PRIMARY := 2.0)
    for pid in raw.get("acceptable_alternatives", []):
        add(pid, "acceptable", GAIN_ACCEPTABLE := 1.0)
    for pid in raw.get("supporting_provisions", []):
        add(pid, "supporting", GAIN_SUPPORTING := 0.0)
    # gold_source_chunks may carry references not listed above (e.g. a whole
    # instrument reference like "fssai:regs/contaminants"); include them as
    # supporting so Recall@K_all covers the complete gold signal.
    for pid in raw.get("gold_source_chunks", []):
        if pid not in seen:
            add(pid, "supporting", 0.0)
    return units


def _norm_section(value: Any) -> str | None:
    """Normalise a section reference to its base number, e.g. '16(2)(ii)' -> '16'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    import re

    m = re.match(r"\d{1,4}", s)
    return m.group(0) if m else None


def _section_from_id(provision_id: str) -> str | None:
    """Fallback: parse a section from the id itself ('fssai:s31(2)' -> '31')."""
    rest = provision_id.split(":", 1)[1] if ":" in provision_id else provision_id
    rest = rest.lower()
    for marker in ("s", "sec", "rule", "order"):
        if rest.startswith(marker) and rest[len(marker):].lstrip("(")[:1].isdigit():
            return _norm_section(rest[len(marker):])
    return _norm_section(rest)


def schema_report() -> dict[str, Any]:
    """AVAILABLE / MISSING gold-signal report for the benchmark (protocol §2)."""
    questions = load_questions()
    missing: dict[str, list[str]] = {}
    available: dict[str, int] = {}
    for proto_key, key in _PROTOCOL_FIELDS.items():
        present = 0
        absent_ids: list[str] = []
        for q in questions:
            val = q.raw.get(key)
            if val in (None, "", [], {}):
                absent_ids.append(q.question_id)
            else:
                present += 1
        available[field] = present
        if absent_ids:
            missing[field] = absent_ids
    return {
        "n_questions": len(questions),
        "available_gold_signal": available,
        "missing_gold_signal": missing,
        "note": (
            "temporal_constraints is empty on all 150 questions (the frozen "
            "benchmark labels 8 questions Temporal via question_type but "
            "carries no expected status); temporal correctness can only be "
            "assessed relative to retrieved payload status, not a gold label."
        ),
    }
