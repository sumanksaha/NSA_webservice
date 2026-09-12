"""Per-task evidence sufficiency rubric (V2 plan item 14).

Evaluates each EvidenceTask's gathered evidence against the 7 signals from
the proposal's sufficiency gate — coverage, relevance, authority,
specificity, completeness, contradiction, temporal validity — as pure
functions over the serialized state (chunk dicts + task dicts).  No LLM,
no I/O: deterministic and unit-testable.

The gate (:func:`app.rag.agent.nodes.evidence_sufficiency_node`) consumes
:meth:`SufficiencyAssessor.assess_task` for the per-task verdicts and
:func:`aggregate_verdicts` for the graph-level routing signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.evidence_task import AnswerContract, EvidenceTask

# --------------------------------------------------------------------------- #
# Thresholds (single tuning point for the rubric)
# --------------------------------------------------------------------------- #

#: Signal thresholds — a signal passes at >= its threshold.
THRESHOLDS: dict[str, float] = {
    "coverage": 0.2,  # >= 1 solid chunk (narrow subquestions need one good provision)
    "relevance": 0.35,  # median retrieval score (reranker-normalized floor)
    "authority": 0.5,  # any statute + no low-authority-only evidence
    "specificity": 0.6,  # section-stamped or numeric evidence share
    "completeness": 1.0,  # all contract required fields retrievable
    "contradiction": 0.0,  # conflict ratio must stay below this
    "temporal": 0.0,  # temporal-restriction conflict ratio below this
}

#: Minimum share of chunks that must be section-stamped (or carry numeric
#: provisions) for the specificity signal to pass.
_SPECIFICITY_SHARE = 0.3

#: Minimum claim-groundedness (share of answer claims entailed by evidence,
#: item 15) for the generated answer to finalize without a targeted retry.
CLAIM_GROUNDEDNESS_THRESHOLD = 0.5

#: Chunk count treated as "full coverage" when normalizing (matches top_k=10).
_COVERAGE_NORM = 4


# --------------------------------------------------------------------------- #
# Authority (V2 plan item 17 — first-class evidence signal)
# --------------------------------------------------------------------------- #

#: Document-type → authority weight (statutes are the corpus's top tier).
_DOCUMENT_TYPE_AUTHORITY: dict[str, float] = {
    "act": 1.0,
    "statute": 1.0,
    "regulation": 0.9,
    "rule": 0.9,
    "code": 0.8,
    "notification": 0.6,
    "order": 0.6,
    "guideline": 0.5,
    "circular": 0.5,
    "faq": 0.3,
    "guidance": 0.35,
    "blog": 0.2,
}

#: Authority-name hints (mirrors the three_stage_reranker hierarchy).
_AUTHORITY_NAME_WEIGHTS: list[tuple[str, float]] = [
    ("supreme court", 1.0),
    ("high court", 0.7),
    ("ministry", 0.5),
    ("government", 0.5),
    ("fssai", 0.9),
    ("food safety", 0.9),
    ("authority", 0.6),
]


def chunk_authority_score(chunk: dict[str, Any]) -> float:
    """0–1 authority weight for one chunk dict (item 17).

    Combines the document-type tier with the authority-name hierarchy.
    Unknown metadata scores 0.5 (neutral — a chunk is not penalized for
    missing metadata, only for *low*-authority metadata).
    """
    doc_type = str(chunk.get("document_type") or "").strip().lower()
    score = _DOCUMENT_TYPE_AUTHORITY.get(doc_type, 0.5)
    authority_name = str(chunk.get("authority") or "").strip().lower()
    if authority_name:
        name_score = 0.5
        for hint, weight in _AUTHORITY_NAME_WEIGHTS:
            if hint in authority_name:
                name_score = max(name_score, weight)
        score = max(score, name_score)
    return min(1.0, score)


def chunk_temporally_invalid(chunk: dict[str, Any]) -> bool:
    """True when a chunk's text marks it repealed/superseded/omitted.

    Scope-free per-chunk check (no task temporal scope needed): repeal
    language means the text describes a provision no longer in force.
    Effective-date-vs-scope conflicts need the task's scope and stay in
    :func:`_temporal_conflicts`.
    """
    return bool(REPEALED_RE.search(str(chunk.get("text") or "")))


# --------------------------------------------------------------------------- #
# Temporal validity
# --------------------------------------------------------------------------- #

#: Phrases that mark a chunk as temporally superseded/restricted.
REPEALED_RE = re.compile(r"\b(repealed|superseded|omitted|substituted by)\b", re.IGNORECASE)
_AMENDED_RE = re.compile(r"\bamended\b", re.IGNORECASE)
#: Effectiveness phrases ("with effect from", "w.e.f.", "effective ... 2021").
_EFFECTIVE_RE = re.compile(
    r"\b(?:with effect from|w\.?e\.?f\.?|effective(?:\s+from)?)\s*:?\s*"
    r"(?P<date>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[ -][A-Za-z]+[ -]\d{4}|\d{4})",
    re.IGNORECASE,
)
#: A 4-digit year in a plausible statutory range.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _temporal_conflicts(chunks: list[dict[str, Any]], task: Any) -> list[str]:
    """Chunk ids of evidence that conflicts with the task's temporal scope.

    Rules (deterministic):
    - repeal/supersede/omit language in a chunk is a conflict (the text
      describes a provision no longer in force); plain "amended" is not;
    - an explicit effective date ("w.e.f. 2021") that contradicts the
      task's temporal-scope year is a conflict.
    """
    if not chunks:
        return []
    scope = str(getattr(task, "temporal_scope", None) or "")
    scope_year = _YEAR_RE.search(scope)
    conflicts: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        cid = str(chunk.get("chunk_id") or "")
        if REPEALED_RE.search(text):
            conflicts.append(cid)
            continue
        m = _EFFECTIVE_RE.search(text)
        if m:
            eff_year = _YEAR_RE.search(m.group("date"))
            if eff_year and scope_year and eff_year.group(0) != scope_year.group(0):
                conflicts.append(cid)
    return conflicts


# --------------------------------------------------------------------------- #
# Contradiction (delegates to the EvidenceVerifier's overlap detector)
# --------------------------------------------------------------------------- #


def as_retrieved_chunks(chunks: list[dict[str, Any]]) -> list[Any]:
    """Convert serialized chunk dicts into ``RetrievedChunk`` objects.

    The verification layer works on dataclass chunks; the agent state
    carries their serialized ``to_dict()`` form.  Conversion is lenient —
    unparsable entries are skipped rather than failing the rubric.
    """
    from app.rag.retrieval.result import RetrievedChunk

    out: list[Any] = []
    for c in chunks:
        if isinstance(c, RetrievedChunk):
            out.append(c)
            continue
        if not isinstance(c, dict):
            continue
        try:
            out.append(
                RetrievedChunk(
                    chunk_id=str(c.get("chunk_id") or ""),
                    score=float(c.get("score") or 0.0),
                    text=str(c.get("text") or ""),
                    section_number=c.get("section_number"),
                    document_type=str(c.get("document_type") or ""),
                    authority=str(c.get("authority") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _contradiction_ratio(chunks: list[dict[str, Any]], task: Any) -> tuple[float, list[dict[str, Any]]]:
    """(conflict_ratio, conflicts) for one task's evidence.

    Wraps :class:`EvidenceVerifier.find_contradictions` on the task's
    evidence (numeric-provision and prohibition/permission conflicts).
    """
    if len(chunks) < 2:
        return 0.0, []
    from app.rag.verification.evidence_verifier import EvidenceVerifier

    verifier = EvidenceVerifier()
    conflicts = verifier.find_contradictions(as_retrieved_chunks(chunks))
    checked = len(chunks) * (len(chunks) - 1) // 2
    ratio = len(conflicts) / checked if checked else 0.0
    return ratio, [
        {
            "chunk_a": c.a.chunk_id,
            "chunk_b": c.b.chunk_id,
            "kind": c.kind,
            "values": list(c.values),
        }
        for c in conflicts
    ]


# --------------------------------------------------------------------------- #
# Completeness (answer contract fields retrievable from the evidence)
# --------------------------------------------------------------------------- #

_FIELD_HINTS: dict[str, list[str]] = {
    "penalty": ["penalty", "fine", "imprisonment", "punishment", "₹", "rs."],
    "section": ["section"],
    "provision": ["section", "provision"],
    "citation": ["section", "act"],
    "offence": ["offence", "contravention", "whoever"],
    "term": ["means", "definition", "refers"],
    "definition": ["means", "definition", "includes"],
    "maximum_or_fixed": ["may extend", "shall be", "fine"],
    "legal_provision": ["section", "act", "rule"],
    "source_provision": ["section"],
    "authority": ["authority", "officer", "commissioner"],
    "procedure": ["shall", "procedure", "application"],
}


def _contract_retrievable(
    contract: AnswerContract | None, chunks: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    """(all_required_fields_hinted, missing_fields) for one task's contract.

    A field is "retrievable" when at least one chunk's text carries one of
    the field's lexical hints.  This is the deterministic floor for the
    completeness signal — the LLM answer itself is checked at claim level.
    """
    if contract is None or not contract.required_fields:
        return True, []
    corpus = "\n".join(str(c.get("text") or "") for c in chunks).lower()
    missing: list[str] = []
    for field_name in contract.required_fields:
        hints = _FIELD_HINTS.get(str(field_name).lower(), [str(field_name).lower()])
        if not any(hint in corpus for hint in hints):
            missing.append(str(field_name))
    return not missing, missing


# --------------------------------------------------------------------------- #
# Rubric result + assessor
# --------------------------------------------------------------------------- #


@dataclass
class TaskSufficiency:
    """One task's 7-signal sufficiency verdict.

    Attributes:
        task_id: The evaluated task.
        sufficient: Aggregate verdict (all gate-level signals pass).
        signals: signal name -> {value, passed, detail}.
        failures: Sorted names of failed signals that are *recoverable*
            (drive targeted retry diagnosis).
        conflicts: Contradiction records (empty when none found).
    """

    task_id: str
    sufficient: bool
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    # Answer-requirement identity (Phase 3): which requirement in the
    # AnswerRequirementGraph this task serves (``EvidenceTask.
    # source_requirement_id``).  ``None`` for tasks planned before the
    # requirement graph existed (backward compatible).
    requirement_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sufficient": self.sufficient,
            "signals": self.signals,
            "failures": self.failures,
            "conflicts": self.conflicts,
            "requirement_id": self.requirement_id,
        }


class SufficiencyAssessor:
    """Assess per-task evidence against the 7-signal rubric."""

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        self.thresholds = {**THRESHOLDS, **(thresholds or {})}

    # ------------------------------------------------------------------ #

    def assess_task(
        self,
        task: EvidenceTask,
        chunks: list[dict[str, Any]],
        top_k: int = 10,
    ) -> TaskSufficiency:
        """Run the 7 signals over one task's evidence chunks."""
        signals: dict[str, dict[str, Any]] = {}
        n = len(chunks)

        # 1. Coverage — normalized chunk count (0 chunks → 0).
        coverage_value = min(1.0, n / _COVERAGE_NORM)
        signals["coverage"] = {
            "value": round(coverage_value, 3),
            "passed": n > 0 and coverage_value >= self.thresholds["coverage"],
            "detail": {"chunks": n},
        }

        # 2. Relevance — median retrieval score (reranker output).
        scores = sorted(
            float(c.get("score") or 0.0) for c in chunks if isinstance(c, dict)
        )
        median_score = scores[len(scores) // 2] if scores else 0.0
        signals["relevance"] = {
            "value": round(median_score, 3),
            "passed": n > 0 and median_score >= self.thresholds["relevance"],
            "detail": {"min": round(min(scores), 3) if scores else 0.0, "max": round(max(scores), 3) if scores else 0.0},
        }

        # 3. Authority — max authority weight in the evidence (item 17).
        authority_values = [chunk_authority_score(c) for c in chunks if isinstance(c, dict)]
        best_authority = max(authority_values) if authority_values else 0.0
        signals["authority"] = {
            "value": round(best_authority, 3),
            "passed": n > 0 and best_authority >= self.thresholds["authority"],
            "detail": {"weights": [round(v, 2) for v in authority_values[:5]]},
        }

        # 4. Specificity — share of section-stamped or numeric-bearing chunks.
        specific = 0
        for c in chunks:
            if not isinstance(c, dict):
                continue
            if c.get("section_number") or _EFFECTIVE_RE.search(str(c.get("text") or "")) or re.search(
                r"\b(₹|rs\.?)\s*[\d,]+", str(c.get("text") or ""), re.IGNORECASE
            ):
                specific += 1
        specificity_share = specific / n if n else 0.0
        signals["specificity"] = {
            "value": round(specificity_share, 3),
            "passed": n > 0 and specificity_share >= _SPECIFICITY_SHARE,
            "detail": {"specific_chunks": specific},
        }

        # 5. Completeness — contract required fields retrievable.
        retrievable, missing_fields = _contract_retrievable(
            getattr(task, "answer_contract", None), chunks
        )
        signals["completeness"] = {
            "value": 1.0 if retrievable else 0.0,
            "passed": retrievable,
            "detail": {"missing_fields": missing_fields},
        }

        # 6. Contradiction — pairwise conflict ratio below threshold.
        ratio, conflicts = _contradiction_ratio(chunks, task)
        signals["contradiction"] = {
            "value": round(ratio, 3),
            "passed": ratio <= self.thresholds["contradiction"],
            "detail": {"pairs_checked": len(chunks) * (len(chunks) - 1) // 2, "conflicts": conflicts},
        }

        # 7. Temporal validity — no superseded/effective-date conflicts.
        temporal_conflicts = _temporal_conflicts(chunks, task)
        temporal_ratio = len(temporal_conflicts) / n if n else 0.0
        signals["temporal"] = {
            "value": round(temporal_ratio, 3),
            "passed": temporal_ratio <= self.thresholds["temporal"],
            "detail": {"conflicting_chunks": temporal_conflicts},
        }

        # Aggregate: every signal must pass.  Failures list drives diagnosis.
        failures = sorted(name for name, sig in signals.items() if not sig["passed"])
        return TaskSufficiency(
            task_id=task.task_id,
            sufficient=not failures,
            signals=signals,
            failures=failures,
            conflicts=signals["contradiction"]["detail"]["conflicts"],
            requirement_id=task_requirement_id(task),
        )


# --------------------------------------------------------------------------- #
# Aggregation → graph-level routing signals
# --------------------------------------------------------------------------- #

#: Signals whose failure means the *evidence set itself* is wrong (not just
#: weak): the answer must not be synthesized from it without recovery.
_CRITICAL_SIGNALS = {"coverage", "authority", "contradiction", "temporal"}

#: Signals that gate synthesis.  ``specificity`` and ``completeness`` are
#: deliberately advisory: missing section stamps / contract-field hints are
#: diagnosed (MISSING_SPECIFICITY → identifier_search) but do not block
#: synthesis — the answer contract is *enforced* post-generation at claim
#: level (item 15), where it can actually fail the answer instead of
#: guessing from chunk text.
GATING_SIGNALS = {"coverage", "relevance", "authority", "contradiction", "temporal"}

#: Temporal/contradiction failures map to dedicated diagnosis failures
#: (consumed by the FailureClassifier taxonomy).
_SIGNAL_TO_FAILURE: dict[str, str] = {
    "coverage": "INSUFFICIENT_EVIDENCE_COVERAGE",
    "relevance": "LOW_RELEVANCE",
    "authority": "INSUFFICIENT_AUTHORITY_SCORE",
    "completeness": "MISSING_SPECIFICITY",
    "specificity": "MISSING_SPECIFICITY",
    "contradiction": "EVIDENCE_CONTRADICTION",
    "temporal": "TEMPORAL_INVALIDITY",
}


def signal_to_failure(signal: str) -> str:
    """Map a failed rubric signal to its FailureClassifier taxonomy name."""
    return _SIGNAL_TO_FAILURE.get(signal, "INSUFFICIENT_EVIDENCE_COVERAGE")


def task_requirement_id(task: Any) -> str | None:
    """Extract the answer-requirement id a task was derived from.

    Reads the first-class ``source_requirement_id`` field (Phase 3).  Falls
    back to the legacy ``requirement_id:{id}`` entity marker so tasks
    serialized before the field existed (in-flight checkpoints, cached
    plans) still resolve; returns ``None`` when neither is present.
    """
    req_id = getattr(task, "source_requirement_id", None)
    if req_id:
        return str(req_id)
    for entity in getattr(task, "entities", None) or []:
        if isinstance(entity, str) and entity.startswith("requirement_id:"):
            legacy = entity.split(":", 1)[1].strip()
            if legacy:
                return legacy
    return None


def aggregate_verdicts(
    verdicts: list[TaskSufficiency],
    *,
    min_sufficient_ratio: float = 0.5,
) -> dict[str, Any]:
    """Fold per-task verdicts into the gate's routing signals.

    Returns:
        ``sufficient`` (share of fully-sufficient tasks meets the ratio),
        ``critical_failure`` (some task failed a signal whose failure means
        the evidence set itself is wrong), ``has_conflicts``, per-task
        ``verdicts`` (for the audit trail / ``task_sufficiency`` channel)
        and the union of ``failure_codes`` for diagnosis.
    """
    if not verdicts:
        return {
            "sufficient": False,
            "critical_failure": False,
            "has_conflicts": False,
            "failed_tasks": [],
            "failure_codes": [],
            "verdicts": [],
        }
    sufficient_count = sum(
        1 for v in verdicts if not (set(v.failures) & GATING_SIGNALS)
    )
    failed_tasks = [v.task_id for v in verdicts if v.failures]
    failure_codes: list[str] = []
    for v in verdicts:
        for sig in v.failures:
            code = signal_to_failure(sig)
            if code not in failure_codes:
                failure_codes.append(code)
    return {
        "sufficient": sufficient_count / len(verdicts) >= min_sufficient_ratio,
        "critical_failure": any(set(v.failures) & _CRITICAL_SIGNALS for v in verdicts),
        "has_conflicts": any(v.conflicts for v in verdicts),
        "failed_tasks": failed_tasks,
        "failure_codes": failure_codes,
        "verdicts": [v.to_dict() for v in verdicts],
    }
