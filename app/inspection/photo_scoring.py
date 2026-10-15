"""Photo-evidence scoring — probabilistic violation assessment (ADR-0011).

A Food Safety Officer photographs the premises; a **decision model** scores that
photo against the photo-visible subset of the Inspection Checklist and returns,
for each candidate answer, the probability that the violation is present.

Why a decision model and not a chat model
-----------------------------------------
``mercury-decide`` answers typed ``noul`` questions with a probability **read out
of the model** rather than prose it wrote itself. Measured on this feature's
probes it beat a chat model (``typesafe/jev-router``) on reliability (6/6 vs 4/6 —
the chat model intermittently returned unparseable output), latency (1–2s vs
2–6s) and cost (free vs ~$0.001/call).

The trade is that a ``noul`` **must** answer true/false — it has no "cannot tell".
Left alone it will interpolate: fed a synthetic diagram it returned
``proper_covered_utensil=0.867``. So every call carries one extra gate question
first, :data:`_GATE_FIELD`, asking whether the image even depicts a premises
interior. Measured separation on real premises vs. non-premises images: **+0.983**
(0.984 / 0.001 / 0.000). Below :data:`GATE_THRESHOLD` the whole assessment is a
recorded abstention and no candidate is scored at all.

Four rules this module exists to enforce:

**One vocabulary.** The candidate answers are *derived* from
``CHECKLIST_RULES`` and ``SPECIAL_VIOLATION_RULES`` (the tables
:func:`app.shared.context_derivers.derive_violations` reads), minus an explicit
exclusion set — never re-declared, so adding a checklist item gets it scored
without touching this file.

**One polarity inversion.** Those two tables disagree: a checklist item is a
violation when its form value is ``"no"``, but ``artificial_colour`` and
``Expired_item`` are violations when it is ``"yes"``. The scorer normalises to a
single ``p_violation`` meaning "probability this violation is present", and
converts back in :func:`form_value_for_violation` — the only place that inversion
exists.

**Absent is not zero.** A gated-off or missing probability is stored ``None``,
never ``0.0``: ``0.0`` is a *compliance finding* ("the premises were clean") and
``None`` is a gap in the evidence. On a blank document scan — a water test report
photographed as evidence — the correct output is a recorded abstention.

**Fail closed, and record it.** An unparseable or partial response becomes a
failed assessment with every probability ``None``, and is still written to
``evidence_assessment`` so a broken pass is auditable rather than invisible.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.shared.context_derivers import CHECKLIST_RULES, SPECIAL_VIOLATION_RULES

logger = logging.getLogger(__name__)

#: Bumped when the questions below change in a way that could move the numbers.
#: Recorded on every assessment so drift is attributable.
PROMPT_VERSION = "v1"

#: The abstention gate. Asked as one extra ``noul`` in the same call; a photo that
#: is not a premises interior scores low and suppresses every candidate.
_GATE_FIELD = "premises_photograph"
_GATE_INSTRUCTIONS = (
    "Answer true ONLY if this image is a real photograph of the interior of a food "
    "business premises showing food handling, food storage, or premises condition. "
    "Answer false for a document, a scan, a form, a diagram, a drawing, a blank frame, "
    "or an exterior street or facade view. Answer false if you cannot tell."
)

#: Gate at or above this counts as a scorable premises photo. Measured separation
#: between a real premises interior and the non-premises probes is +0.983, so this
#: sits in a wide empty band rather than near either cluster.
GATE_THRESHOLD = 0.50

#: Checklist fields a photograph cannot establish. ``Pest_report`` and
#: ``Water_report`` ask whether a *document* was produced; a photo of premises
#: cannot answer that, and scoring them would manufacture violations.
PHOTO_UNOBSERVABLE_FIELDS: frozenset[str] = frozenset({"Pest_report", "Water_report"})

#: Every candidate answer, in one order, derived rather than re-declared.
SCORABLE_FIELDS: tuple[str, ...] = tuple(
    f for f in CHECKLIST_RULES if f not in PHOTO_UNOBSERVABLE_FIELDS
) + tuple(f for f in SPECIAL_VIOLATION_RULES if f not in PHOTO_UNOBSERVABLE_FIELDS)

#: Fields whose stored form value records a violation as ``"yes"`` rather than
#: ``"no"``. Mirrors ``derive_violations``; asserted against it in the tests.
INVERTED_FORM_VALUE_FIELDS: frozenset[str] = frozenset(SPECIAL_VIOLATION_RULES)

_CRITERION = {**CHECKLIST_RULES, **SPECIAL_VIOLATION_RULES}


def _question_for(field: str) -> str:
    """Ask for *presence* of the violation, so ``noul`` P(true) is ``p_violation``."""
    title, observation = _CRITERION[field]
    return (
        f"Answer true ONLY if the photograph positively shows that {observation} "
        f"({title}). If the subject is not visible in the frame, answer false. "
        "Do not guess."
    )


def build_questions() -> dict[str, dict[str, str]]:
    """The full question map for one call: the gate, then every candidate.

    One call, so the gate and the candidates cannot disagree about which image
    was scored.
    """
    return {
        _GATE_FIELD: {"type": "noul", "instructions": _GATE_INSTRUCTIONS},
        **{f: {"type": "noul", "instructions": _question_for(f)} for f in SCORABLE_FIELDS},
    }


# --------------------------------------------------------------------------- #
# Polarity — the single inversion point
# --------------------------------------------------------------------------- #
def form_value_for_violation(field: str, *, violation_present: bool) -> str:
    """Return the checklist form value that records ``violation_present``.

    The inverse of :func:`app.shared.context_derivers.derive_violations`: a
    flagged checklist item is recorded ``"no"``, while ``artificial_colour`` and
    ``Expired_item`` are recorded ``"yes"``. Kept here so the scorer's uniform
    ``p_violation`` never leaks an inverted reading into a stored form value.
    """
    if field in INVERTED_FORM_VALUE_FIELDS:
        return "yes" if violation_present else "no"
    return "no" if violation_present else "yes"


# --------------------------------------------------------------------------- #
# Result shape
# --------------------------------------------------------------------------- #
@dataclass
class ScoredItem:
    """One candidate answer's assessment."""

    field: str
    observable: bool
    p_violation: float | None
    rationale: str = ""


@dataclass
class ScoringResult:
    """Outcome of one scoring run.

    ``ok`` false means the run failed closed: every field is unobservable with no
    probability. ``gate_passed`` false means the model judged the image not to be
    a scorable premises photograph — a recorded abstention, distinct from a clean
    premises.
    """

    ok: bool
    gate_passed: bool
    items: list[ScoredItem] = field(default_factory=list)
    provider_model: str | None = None
    resolved_model: str | None = None
    token_usage: int | None = None
    latency_ms: int | None = None
    error: str | None = None

    @property
    def overall_observable(self) -> bool:
        """Back-compat alias: the image was scorable at all."""
        return self.ok and self.gate_passed

    def by_field(self) -> dict[str, ScoredItem]:
        return {i.field: i for i in self.items}

    def suggested_fields(self, threshold: float) -> list[str]:
        """Fields the model flags as violations at or above ``threshold``.

        Advisory only. A gated-off run and a failed run both suggest nothing.
        """
        if not self.ok or not self.gate_passed:
            return []
        return [
            i.field
            for i in self.items
            if i.observable and i.p_violation is not None and i.p_violation >= threshold
        ]


def _blank_items() -> list[ScoredItem]:
    return [ScoredItem(field=f, observable=False, p_violation=None) for f in SCORABLE_FIELDS]


def _failed(reason: str, *, provider_model: str | None = None, latency_ms: int | None = None) -> ScoringResult:
    """The fail-closed result: gate closed, every field unobservable, no probabilities."""
    return ScoringResult(
        ok=False,
        gate_passed=False,
        items=_blank_items(),
        provider_model=provider_model,
        latency_ms=latency_ms,
        error=reason,
    )


def _gated_out(provider_model: str | None = None, latency_ms: int | None = None, note: str = "") -> ScoringResult:
    """A successful call whose gate says the image is not a scorable premises photo."""
    return ScoringResult(
        ok=True,
        gate_passed=False,
        items=_blank_items(),
        provider_model=provider_model,
        latency_ms=latency_ms,
        error=note or None,
    )


def _clamp(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def parse_decision_response(raw: Any) -> ScoringResult:
    """Turn a ``/v1/decisions`` answers map into a :class:`ScoringResult`.

    Fails closed. The gate is evaluated first: below :data:`GATE_THRESHOLD` the
    candidates are not read at all, so a non-premises image can never contribute
    a candidate probability.
    """
    if not isinstance(raw, dict):
        return _failed("decision response was not an object")

    gate_answer = raw.get(_GATE_FIELD)
    gate_prob = _clamp(gate_answer.get("noul")) if isinstance(gate_answer, dict) else None
    if gate_prob is None:
        # A missing gate is not a pass. Defaulting it open would let a malformed
        # response read as a scorable premises.
        return _failed(f"gate question {_GATE_FIELD!r} missing or non-numeric")
    if gate_prob < GATE_THRESHOLD:
        return _gated_out(
            note=(
                f"image is not a scorable premises photograph "
                f"(gate {gate_prob:.3f} < {GATE_THRESHOLD}); no candidate scored"
            ),
        )

    items: list[ScoredItem] = []
    missing: list[str] = []
    for name in SCORABLE_FIELDS:
        answer = raw.get(name)
        p = _clamp(answer.get("noul")) if isinstance(answer, dict) else None
        if p is None:
            missing.append(name)
            items.append(ScoredItem(field=name, observable=False, p_violation=None))
        else:
            items.append(ScoredItem(field=name, observable=True, p_violation=p))

    return ScoringResult(
        ok=True,
        gate_passed=True,
        items=items,
        error=f"model returned no probability for {len(missing)} field(s): {', '.join(missing)}" if missing else None,
    )


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class PhotoEvidenceScorer:
    """Scores a premises photograph against the photo-visible checklist subset.

    The HTTP transport is plain module-level ``httpx`` calls, which the
    test-suite monkeypatches at the ``httpx.Client`` seam. Nothing about
    scoring is hard-wired to that, but there is deliberately no client-injection
    parameter: the only collaborators are the stored image URL and the decision
    endpoint.
    """

    # -- configuration ---------------------------------------------------- #
    @staticmethod
    def _settings() -> dict[str, Any]:
        """Resolve config through the typed accessors.

        Deliberately not ``cfg.get_str`` + a manual ``float()``: the seam coerces
        a malformed value to the declared default, whereas string-then-cast
        raises. That distinction bites outside an app context — the worker path —
        where ``cfg`` reads ``os.environ`` directly and sanitises nothing.
        """
        from app.shared.config import cfg

        return {
            "model": cfg.photo_scoring_model or "inception/mercury-decide:free",
            "endpoint": cfg.photo_scoring_endpoint or "https://openrouter.ai/api/v1/systemone",
            # The decision endpoint is reached through OpenRouter with the same
            # key the rest of the AI surface uses — no new secret to provision.
            "api_key": cfg.get_str("AI_ASSISTANT_API_KEY"),
            "timeout": float(cfg.photo_scoring_timeout),
            "max_bytes": int(cfg.photo_scoring_max_bytes),
        }

    @staticmethod
    def fetch_image_as_data_uri(url: str, *, timeout: float = 30.0, max_bytes: int = 5 * 1024 * 1024) -> str:
        """Fetch *url* and return a base64 data URI.

        The decision endpoint takes base64 data URLs only — remote URLs are not
        supported — so the stored Cloudinary/R2 URL has to be fetched here.

        Raises ``ValueError`` when the response is not a usable image.
        """
        import base64
        import httpx

        with httpx.Client(timeout=timeout) as c:
            resp = c.get(url)
        if resp.status_code != 200:
            raise ValueError(f"could not fetch image for scoring (HTTP {resp.status_code})")
        if len(resp.content) > max_bytes:
            raise ValueError(f"image is {len(resp.content)} bytes, over the {max_bytes} byte scoring limit")
        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ValueError(f"stored evidence is not an image (content-type {content_type!r})")
        b64 = base64.b64encode(resp.content).decode()
        return f"data:{content_type};base64,{b64}"

    # -- scoring ---------------------------------------------------------- #
    def score_url(self, image_url: str) -> ScoringResult:
        """Fetch and score a remote image. Never raises for provider/parse problems."""
        cfg = self._settings()
        model, endpoint = cfg["model"], cfg["endpoint"]

        if not cfg["api_key"]:
            return _failed("no API key for the decision endpoint", provider_model=model)
        if not image_url.startswith(("https://", "http://")):
            # Only cloud-backed evidence is scoreable: an inspection photo keeps a
            # local temp path here, which the remote endpoint cannot fetch.
            return _failed(
                "evidence has no remote image URL to score (filepath is a local path)",
                provider_model=model,
            )

        started = time.monotonic()
        try:
            data_uri = self.fetch_image_as_data_uri(
                image_url, timeout=cfg["timeout"], max_bytes=cfg["max_bytes"],
            )
        except Exception as exc:
            return _failed(f"could not read image: {exc}", provider_model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))

        payload = {
            "model": model,
            "state": [{"type": "image_url", "image_url": {"url": data_uri}}],
            "questions": build_questions(),
        }
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.gov.in",
            "X-Title": "NSA Webservice",
        }

        try:
            import httpx

            with httpx.Client(timeout=cfg["timeout"]) as c:
                resp = c.post(endpoint, headers=headers, json=payload)
            if resp.status_code != 200:
                return _failed(f"decision endpoint HTTP {resp.status_code}", provider_model=model,
                               latency_ms=int((time.monotonic() - started) * 1000))
            body = resp.json()
        except Exception as exc:
            logger.exception("Photo scoring request failed")
            return _failed(f"provider error: {exc}", provider_model=model,
                           latency_ms=int((time.monotonic() - started) * 1000))

        # Everything below touches the decoded body, so it must be a mapping.
        # A gateway can answer 200 with a JSON array or a bare string; without
        # this the `.get` below raises AttributeError, which escapes this method
        # and costs the caller its audit row instead of a recorded failure.
        if not isinstance(body, dict):
            return _failed(
                f"decision endpoint returned {type(body).__name__}, expected a JSON object",
                provider_model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        result = parse_decision_response(body.get("answers"))
        result.provider_model = model
        result.resolved_model = body.get("model") if isinstance(body.get("model"), str) else None
        result.latency_ms = latency_ms
        usage = body.get("usage")
        if isinstance(usage, dict):
            result.token_usage = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or None
        else:
            result.token_usage = None
        return result

    def score(self, evidence: Any) -> ScoringResult:
        """Score an :class:`~app.models.document.Evidence` row by its stored URL."""
        from app.shared.config import cfg

        if not bool(cfg.photo_scoring_enabled):
            raise ValueError("Photo scoring is disabled (PHOTO_SCORING_ENABLED=false).")
        return self.score_url((evidence.filepath or "").strip())

    # -- persistence ------------------------------------------------------ #
    def build_assessment(self, evidence_id: str, adjudication_id: int | None, result: ScoringResult) -> Any:
        """Build an unsaved :class:`EvidenceAssessment` for *result*.

        Kept separate from the commit so the caller can persist the photo and its
        assessment in one transaction — the pre-save flow scores after the image is
        in storage but before the evidence row lands.
        """
        from app.models import EvidenceAssessment

        from app.shared.config import cfg

        payload = {
            "gate_passed": result.gate_passed,
            "items": [
                {
                    "field": i.field,
                    "observable": i.observable,
                    "p_violation": i.p_violation,
                }
                for i in result.items
            ],
        }
        return EvidenceAssessment(
            evidence_id=evidence_id,
            adjudication_id=adjudication_id,
            payload=json.dumps(payload),
            overall_observable=result.overall_observable,
            suggested_fields=json.dumps(result.suggested_fields(float(cfg.photo_scoring_threshold))),
            provider_model=result.provider_model,
            resolved_model=result.resolved_model,
            prompt_version=PROMPT_VERSION,
            token_usage=result.token_usage,
            latency_ms=result.latency_ms,
            error=result.error,
        )

    def assess(self, evidence: Any) -> tuple[Any, ScoringResult]:
        """Score *evidence* and append an assessment row."""
        from app.extensions import db

        result = self.score(evidence)
        assessment = self.build_assessment(evidence.id, getattr(evidence, "adjudication_id", None), result)
        db.session.add(assessment)
        db.session.commit()
        return assessment, result


__all__ = [
    "GATE_THRESHOLD",
    "INVERTED_FORM_VALUE_FIELDS",
    "PHOTO_UNOBSERVABLE_FIELDS",
    "PROMPT_VERSION",
    "SCORABLE_FIELDS",
    "PhotoEvidenceScorer",
    "ScoredItem",
    "ScoringResult",
    "build_questions",
    "form_value_for_violation",
    "parse_decision_response",
]
