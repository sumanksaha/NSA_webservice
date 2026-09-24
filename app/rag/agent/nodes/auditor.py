"""Legal auditor — deterministic critique of structured arguments (Phase 1).

Roadmap §32.2: the auditor behaves as a rigorous legal reviewer and emits
*structured defects*, never just a confidence number.  This pass is fully
deterministic (no LLM): it checks the :class:`StructuredLegalArgument`
against the evidence texts for missed exceptions (including stacked /
partially covered markers), invalid citations (fail-closed on empty
evidence), unsupported applications, unhandled definitions, empty scope,
and conclusions that do not ground in the evidence.

The Experiment D harness (Condition C) owns the revision loop — it
re-invokes the reasoner with the defect notes when ``status`` is ``FAIL``,
capped by ``reasoning_path.DEFAULT_MAX_REVISIONS``.  Graph wiring
(``auditor_node`` reading ``structured_argument`` from state) lands in
Phase 3; until the state gains those fields the node is a no-op returning
no updates.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Mapping
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_EXCEPTION_RE = re.compile(
    r"\bexcept\b|\bnotwithstanding\b|\bprovided that\b|\bsubject to\b"
    r"|\bunless\b|\bproviso\b|\bdoes not apply\b|\bshall not apply\b"
)
_DEFINITION_RE = re.compile(r""""[^"]{1,80}"\s+means\b|'[^']{1,80}'\s+means\b|\bfor the purposes of\b""")
# NOTE: these intentionally duplicate the marker lists in
# evaluation/answer_error_taxonomy.py — that module is research-side
# (not installed) and checks context-vs-answer on both sides, while this
# seam checks evidence-side presence only.  Keep them in sync by intent,
# not by import (app/ must never import evaluation/).


class AuditDefect(BaseModel):
    """One concrete defect with its required correction."""

    defect_type: Literal[
        "missed_exception",
        "unsupported_application",
        "definition_mismatch",
        "invalid_citation",
        "incomplete_scope",
        "unsupported_certainty",
    ]
    severity: Literal["critical", "minor"]
    provision_reference: str | None = None
    explanation: str
    required_correction: str


class AuditResult(BaseModel):
    """Auditor verdict: PASS with no defects, else FAIL + defects."""

    status: Literal["PASS", "FAIL"]
    defects: list[AuditDefect] = Field(default_factory=list)


class AuditResultDict(TypedDict, total=False):
    """JSON-serializable audit verdict for state/checkpoint seams.

    The graph state and checkpointer carry ``model_dump()`` dicts (never
    models), so callers across the seam take ``AuditResultDict |
    AuditResult | None`` instead of bare ``dict``/``Any``.
    """

    status: Literal["PASS", "FAIL"]
    defects: list[dict[str, Any]]


def audit_argument(
    argument: Any,
    evidence_texts: Mapping[str, str] | None = None,
    known_provisions: Collection[str] | None = None,
) -> AuditResult:
    """Critique ``argument`` against ``evidence_texts`` (id → text).

    Accepts a :class:`StructuredLegalArgument` or its ``model_dump()``
    dict.  Never raises on caller-controlled input — unparseable
    arguments fail closed with an ``incomplete_scope`` defect.
    """
    evidence: dict[str, str] = dict(evidence_texts or {})
    arg = _coerce(argument)
    if arg is None:
        return AuditResult(
            status="FAIL",
            defects=[
                AuditDefect(
                    defect_type="incomplete_scope",
                    severity="critical",
                    explanation="argument is not a valid structured argument",
                    required_correction="rebuild the structured argument before answering",
                )
            ],
        )

    applicable = list(arg.get("applicable_provisions") or [])
    citations = list(arg.get("supporting_citations") or [])
    exceptions = list(arg.get("exceptions_considered") or [])
    definitions = dict(arg.get("definitions_applied") or {})
    conditions = list(arg.get("condition_evaluations") or [])
    known = set(known_provisions or []) | set(applicable) | set(evidence)

    defects: list[AuditDefect] = []

    if not applicable:
        defects.append(
            AuditDefect(
                defect_type="incomplete_scope",
                severity="critical",
                explanation="no applicable provisions identified",
                required_correction="identify the governing provision before concluding",
            )
        )

    exc_ids = [pid for pid, text in evidence.items() if _EXCEPTION_RE.search(text.lower())]
    uncovered = [pid for pid in exc_ids if not _exception_covered(pid, exceptions)]
    if uncovered:
        defects.append(
            AuditDefect(
                defect_type="missed_exception",
                severity="critical",
                provision_reference=uncovered[0],
                explanation=(
                    f"evidence states an exception/proviso ({uncovered[0]}) "
                    "the argument never considers"
                ),
                required_correction="determine whether the exception applies before concluding",
            )
        )

    # Always validate citations — an empty evidence map must not let every
    # citation through (fail-closed against known_provisions ∪ applicable).
    for cite in citations:
        if cite not in known:
            defects.append(
                AuditDefect(
                    defect_type="invalid_citation",
                    severity="critical",
                    provision_reference=cite,
                    explanation=f"citation {cite} maps to no evidence or applicable provision",
                    required_correction="cite only provisions present in the evidence",
                )
            )

    for cond in conditions:
        cond = cond if isinstance(cond, dict) else {}
        if cond.get("status") == "satisfied" and not str(cond.get("fact_reference") or "").strip():
            defects.append(
                AuditDefect(
                    defect_type="unsupported_application",
                    severity="critical",
                    explanation=f"condition {cond.get('condition_id')} marked satisfied with no supporting fact",
                    required_correction="map each satisfied condition to an explicit fact or mark it unknown",
                )
            )

    if any(_DEFINITION_RE.search(t.lower()) for t in evidence.values()) and not definitions:
        defects.append(
            AuditDefect(
                defect_type="definition_mismatch",
                severity="minor",
                explanation="evidence defines a term the argument never applies",
                required_correction="resolve defined terms before applying the downstream rule",
            )
        )

    conclusion = str(arg.get("derived_conclusion") or "").strip()
    if applicable and not conclusion:
        defects.append(
            AuditDefect(
                defect_type="incomplete_scope",
                severity="critical",
                explanation="applicable provisions identified but no derived conclusion stated",
                required_correction="state an explicit conclusion grounded in the cited provisions",
            )
        )
    elif conclusion and evidence:
        # Deterministic conclusion↔evidence check: a long conclusion must
        # share content with the evidence (paraphrase-tolerant prefix match).
        # Short conclusions (≤2 content words) are skipped — they cannot
        # carry a meaningful quote signal.
        conc_words = set(re.findall(r"[a-z]{4,}", conclusion.lower()))
        if len(conc_words) >= 3:
            ev_text = " ".join(evidence.values()).lower()
            if not any(w in ev_text or w[:5] in ev_text for w in conc_words):
                defects.append(
                    AuditDefect(
                        defect_type="unsupported_certainty",
                        severity="critical",
                        explanation="derived conclusion shares no content with the evidence texts",
                        required_correction="ground the conclusion in a quoted span of the evidence",
                    )
                )

    if not defects:
        return AuditResult(status="PASS")
    return AuditResult(status="FAIL", defects=defects)


def _exception_covered(pid: str, exceptions: list[Any]) -> bool:
    """Whether ``exceptions`` addresses the exception-bearing provision ``pid``.

    Exception-stacking silence: a single non-empty ``exceptions`` list used to
    silence every exception marker in the evidence.  Each exception-bearing
    evidence id must be referenced (full id or bare section token), otherwise
    uncovered provisions emit ``missed_exception``.
    """
    if not exceptions:
        return False
    pid_l = pid.lower()
    tail = pid.split("::")[-1].lower() if "::" in pid else ""
    tail_re = re.compile(rf"(?<!\d){re.escape(tail)}(?!\d)") if tail else None
    for ex in exceptions:
        try:
            blob = json.dumps(ex, default=str).lower()
        except (TypeError, ValueError):
            blob = str(ex).lower()
        if pid_l in blob:
            return True
        if tail_re is not None and tail_re.search(blob):
            return True
    return False


def _coerce(argument: Any) -> dict[str, Any] | None:
    if isinstance(argument, dict):
        return argument
    model_dump = getattr(argument, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return None
        return dumped if isinstance(dumped, dict) else None
    return None


def auditor_node(state: dict[str, Any]) -> dict[str, Any]:
    """Graph adapter (Phase 3 wiring lands with the state fields).

    Reads ``structured_argument`` + ``legal_unit_evidence`` when present;
    returns no updates otherwise so the current graph is unaffected.
    Revision counting belongs to ``route_after_audit``, not to this node, so
    only ``audit_result`` is written.
    """
    raw_argument = state.get("structured_argument")
    if not raw_argument:
        return {}
    evidence_items = state.get("legal_unit_evidence") or []
    evidence = {str(item.get("id", i)): str(item.get("text", "")) for i, item in enumerate(evidence_items)}
    # Citations may use chunk ids or canonical unit ids (ACT::SEC) — accept both.
    known = set(evidence) | {str(item.get("unit", "")) for item in evidence_items} - {""}
    result = audit_argument(raw_argument, evidence, known_provisions=known)
    return {"audit_result": result.model_dump()}
