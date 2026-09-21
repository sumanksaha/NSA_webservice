"""Timeline event extraction engine (plan.md Phase 13).

Automatically derives milestone events for a case from date fields across
``CaseFile``, ``Adjudication``, ``Inspection``, ``Sample``, ``Annexure``,
and ``Evidence`` records, then serves them to the vertical-timeline +
Gantt-chart UI (``app/timeline/templates/timeline/index.html``).

Persistence note
----------------
The ``timeline_event`` table's ``case_id`` column is a NOT NULL foreign key
to ``case_files.id`` (no ``adjudication_id`` column exists), so only
**case_file** timelines are persisted to the database.  Adjudication
timelines are computed on the fly (ephemeral) and never written — the API
still serves them identically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.extensions import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Canonical event types + UI metadata
# --------------------------------------------------------------------------- #

EVENT_CASE_CREATED = "case_created"
EVENT_COMPLAINT = "complaint"
EVENT_INSPECTION = "inspection"
EVENT_SAMPLING = "sampling"
EVENT_LAB_DISPATCH = "lab_dispatch"
EVENT_LAB_RECEIPT = "lab_receipt"
EVENT_LAB_REPORT = "lab_report"
EVENT_NOTICE = "notice"
EVENT_REPLY = "reply"
EVENT_AUTHORIZATION = "authorization"
EVENT_COMPLIANCE = "compliance"
EVENT_ANNEXURE = "annexure"
EVENT_EVIDENCE = "evidence"
EVENT_PETITION_DEADLINE = "petition_deadline"
EVENT_GENERATION_ALLOWED = "generation_allowed"

#: Legal deadlines (in days) driving the computed Gantt milestones.
#: - Petition must be filed within 365 days of the analyst (sample) report.
#: - Permission/petition files cannot be generated within 30 days of the
#:   report handover (max of retailer/manufacturer receive dates) — the
#:   30-day appeal window under S.46(4).
PETITION_LIMIT_DAYS = 365
APPEAL_EMBARGO_DAYS = 30

#: Human-readable label, FontAwesome icon, and CSS color per event type.
_EVENT_META: dict[str, tuple[str, str, str]] = {
    EVENT_CASE_CREATED: ("Case opened", "fa-file-circle-plus", "#0b3d91"),
    EVENT_COMPLAINT: ("Complaint lodged", "fa-triangle-exclamation", "#b3261e"),
    EVENT_INSPECTION: ("Inspection", "fa-magnifying-glass-chart", "#0b6e4f"),
    EVENT_SAMPLING: ("Sampling", "fa-vial", "#c77d0a"),
    EVENT_LAB_DISPATCH: ("Sample dispatched to lab", "fa-paper-plane", "#5a4fcf"),
    EVENT_LAB_RECEIPT: ("Lab receipt", "fa-inbox", "#5a4fcf"),
    EVENT_LAB_REPORT: ("Lab report", "fa-file-lines", "#8e24aa"),
    EVENT_NOTICE: ("Notice / directive issued", "fa-envelope-open-text", "#c77d0a"),
    EVENT_REPLY: ("Reply received", "fa-reply", "#0b6e4f"),
    EVENT_AUTHORIZATION: ("Authorization issued", "fa-file-signature", "#0b3d91"),
    EVENT_COMPLIANCE: ("Compliance deadline", "fa-calendar-check", "#b3261e"),
    EVENT_ANNEXURE: ("Annexure", "fa-paperclip", "#455a64"),
    EVENT_EVIDENCE: ("Evidence", "fa-folder-open", "#455a64"),
    EVENT_PETITION_DEADLINE: ("Petition filing deadline", "fa-scale-balanced", "#b3261e"),
    EVENT_GENERATION_ALLOWED: ("Permission/petition allowed from", "fa-lock-open", "#0b6e4f"),
}


# --------------------------------------------------------------------------- #
# Legal-deadline helpers (shared with the case-file generation gate)
# --------------------------------------------------------------------------- #


def _coerce_dt(value) -> datetime | None:
    """Coerce a date/datetime/ISO string to a datetime (None if unparseable)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and not isinstance(value, str):
        from datetime import datetime as _dt

        return _dt.combine(value, _dt.min.time())
    try:
        from app.utils.filters import parse_date as _parse

        return _parse(value)
    except Exception:
        return None


def petition_deadline_for(analyst_report_date) -> datetime | None:
    """Return analyst_report_date + 365 days (None when no report date)."""
    base = _coerce_dt(analyst_report_date)
    if base is None:
        return None
    return base + timedelta(days=PETITION_LIMIT_DAYS)


def handover_max_date(retailer_receive_date, manufacturer_receive_date) -> datetime | None:
    """Return the later of the two report-handover dates (None if neither)."""
    candidates = [
        _coerce_dt(retailer_receive_date),
        _coerce_dt(manufacturer_receive_date),
    ]
    valid = [d for d in candidates if d is not None]
    if not valid:
        return None
    try:
        return max(valid)
    except TypeError:
        # Mixed naive/aware datetimes — compare by date part as a fallback.
        return max(valid, key=lambda d: d.date() if hasattr(d, "date") else d)


def generation_allowed_from(retailer_receive_date, manufacturer_receive_date) -> datetime | None:
    """Return handover_max + 30 days (None when no handover date)."""
    base = handover_max_date(retailer_receive_date, manufacturer_receive_date)
    if base is None:
        return None
    return base + timedelta(days=APPEAL_EMBARGO_DAYS)


def generation_gate(
    retailer_receive_date, manufacturer_receive_date, now: datetime | None = None
) -> dict:
    """Evaluate the 30-day appeal-window gate for petition/permission files.

    Returns ``{"blocked", "earliest", "handover", "today"}`` where
    ``blocked`` is True when *now* (default: today UTC) falls before
    ``earliest`` (handover_max + 30 days).  Date-part comparison is used
    so naive/aware mixes never crash; generation is allowed on the
    30th day itself.
    """
    handover = handover_max_date(retailer_receive_date, manufacturer_receive_date)
    earliest = generation_allowed_from(retailer_receive_date, manufacturer_receive_date)
    today = now if now is not None else datetime.now(UTC)
    blocked = False
    if earliest is not None:
        try:
            blocked = today.date() < earliest.date()
        except Exception:
            blocked = today < earliest
    return {"blocked": blocked, "earliest": earliest, "handover": handover, "today": today}


@dataclass
class TimelineEntry:
    """A single computed milestone for a case."""

    event_type: str
    timestamp: datetime
    description: str
    document_ref: str | None = None  # "annexure:<id>" | "evidence:<id>"
    document_label: str | None = None


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class TimelineEngine:
    """Extract, persist, and validate timeline milestones for a case."""

    # -- Extraction --------------------------------------------------------- #

    def extract(self, case) -> list[TimelineEntry]:
        """Compute milestone entries for a ``CaseFile`` or ``Adjudication``.

        Args:
            case: A ``CaseFile`` or ``Adjudication`` model instance.

        Returns:
            Chronologically sorted list of :class:`TimelineEntry`.
        """
        from app.models import Adjudication, CaseFile

        if isinstance(case, CaseFile):
            entries = self._case_file_entries(case)
        elif isinstance(case, Adjudication):
            entries = self._adjudication_entries(case)
        else:
            logger.warning("TimelineEngine.extract: unsupported case type %s", type(case).__name__)
            return []

        entries.extend(self._annexure_entries(case))
        entries.extend(self._evidence_entries(case))
        return self._sort_entries(entries)

    def _case_file_entries(self, case) -> list[TimelineEntry]:
        """Milestones from a CaseFile's legal-proceeding date fields."""
        from app.models import Sample

        entries: list[TimelineEntry] = []
        if case.created_at:
            entries.append(TimelineEntry(EVENT_CASE_CREATED, case.created_at, "Case file opened"))
        if case.inspection_date:
            entries.append(
                TimelineEntry(
                    EVENT_INSPECTION,
                    case.inspection_date,
                    "Inspection conducted by " + (case.food_safety_officer_name or "FSO"),
                )
            )

        # Linked Sample (case_file.sample_id) — collection/dispatch milestones.
        sample = None
        if case.sample_id is not None:
            sample = db.session.get(Sample, case.sample_id)
        if sample is not None and sample.collection_date:
            entries.append(
                TimelineEntry(
                    EVENT_SAMPLING,
                    sample.collection_date,
                    f"Sample collected — {sample.sample_name or sample.sample_code}",
                )
            )
        if sample is not None and sample.submission_date:
            entries.append(
                TimelineEntry(
                    EVENT_LAB_DISPATCH,
                    sample.submission_date,
                    f"Sample {sample.sample_code} dispatched to laboratory",
                )
            )

        if case.sample_submission_date:
            entries.append(
                TimelineEntry(
                    EVENT_SAMPLING,
                    case.sample_submission_date,
                    "Sample submitted to laboratory",
                )
            )
        if case.do_receipt_date:
            entries.append(
                TimelineEntry(EVENT_LAB_RECEIPT, case.do_receipt_date, "Laboratory received sample (DO receipt)")
            )
        if case.analyst_report_date:
            entries.append(
                TimelineEntry(
                    EVENT_LAB_REPORT,
                    case.analyst_report_date,
                    "Analyst report issued" + (f" — {case.analyst_report_no}" if case.analyst_report_no else ""),
                )
            )
        if case.directive_letter_date:
            entries.append(
                TimelineEntry(
                    EVENT_NOTICE,
                    case.directive_letter_date,
                    "Directive letter issued" + (f" — {case.directive_letter_no}" if case.directive_letter_no else ""),
                )
            )
        if getattr(case, "authorization_date", None):
            entries.append(
                TimelineEntry(
                    EVENT_AUTHORIZATION,
                    case.authorization_date,
                    "Authorization issued by the Designated Officer",
                )
            )
        if case.retailer_report_receive_date:
            entries.append(TimelineEntry(EVENT_REPLY, case.retailer_report_receive_date, "Retailer reply received"))
        if case.manufacturer_report_receive_date:
            entries.append(
                TimelineEntry(EVENT_REPLY, case.manufacturer_report_receive_date, "Manufacturer reply received")
            )
        entries.extend(self._deadline_entries(case))
        return entries

    def _deadline_entries(self, case) -> list[TimelineEntry]:
        """Computed legal-deadline milestones for a CaseFile.

        - Petition filing deadline: analyst_report_date + 365 days.
        - Permission/petition earliest date: max(handover dates) + 30 days.
        """
        entries: list[TimelineEntry] = []
        analyst_date = getattr(case, "analyst_report_date", None)
        deadline = petition_deadline_for(analyst_date)
        if deadline is not None:
            base = _coerce_dt(analyst_date)
            base_str = base.strftime("%d-%m-%Y") if base else ""
            entries.append(
                TimelineEntry(
                    EVENT_PETITION_DEADLINE,
                    deadline,
                    f"Petition must be filed by {deadline.strftime('%d-%m-%Y')} "
                    f"(365 days from analyst report {base_str})".strip(),
                )
            )
        earliest = generation_allowed_from(
            getattr(case, "retailer_report_receive_date", None),
            getattr(case, "manufacturer_report_receive_date", None),
        )
        if earliest is not None:
            handover = handover_max_date(
                getattr(case, "retailer_report_receive_date", None),
                getattr(case, "manufacturer_report_receive_date", None),
            )
            handover_str = handover.strftime("%d-%m-%Y") if handover else ""
            entries.append(
                TimelineEntry(
                    EVENT_GENERATION_ALLOWED,
                    earliest,
                    f"Permission/petition may be generated on/after {earliest.strftime('%d-%m-%Y')} "
                    f"(30 days after report handover {handover_str})".strip(),
                )
            )
        return entries

    def _adjudication_entries(self, adj) -> list[TimelineEntry]:
        """Milestones from an Adjudication + its linked Inspection records."""
        from app.models import Inspection

        entries: list[TimelineEntry] = []
        if adj.created_at:
            entries.append(TimelineEntry(EVENT_CASE_CREATED, adj.created_at, "Case opened"))
        if adj.Complaint_date:
            entries.append(
                TimelineEntry(
                    EVENT_COMPLAINT,
                    adj.Complaint_date,
                    "Complaint lodged" + (f" — {adj.fbo_name}" if adj.fbo_name else ""),
                )
            )
        if adj.authorization_date:
            entries.append(TimelineEntry(EVENT_AUTHORIZATION, adj.authorization_date, "Authorization issued"))
        if adj.First_inspection_date:
            entries.append(
                TimelineEntry(
                    EVENT_INSPECTION,
                    adj.First_inspection_date,
                    "First inspection conducted",
                )
            )
        if adj.inspection_date:
            entries.append(
                TimelineEntry(
                    EVENT_INSPECTION,
                    adj.inspection_date,
                    "Inspection conducted by " + (adj.food_safety_officer or "FSO"),
                )
            )
        if adj.compliance_deadline:
            entries.append(TimelineEntry(EVENT_COMPLIANCE, adj.compliance_deadline, "Compliance deadline"))

        # Inspections that produced this adjudication (inspection.adjudication_id).
        linked = Inspection.query.filter_by(adjudication_id=adj.id).all()
        for inspection in linked:
            if inspection.inspection_date:
                entries.append(
                    TimelineEntry(
                        EVENT_INSPECTION,
                        inspection.inspection_date,
                        f"Inspection {inspection.inspection_code} — {inspection.fbo_name or adj.fbo_name or 'FBO'}",
                    )
                )
            if inspection.compliance_deadline:
                entries.append(
                    TimelineEntry(
                        EVENT_COMPLIANCE,
                        inspection.compliance_deadline,
                        f"Compliance deadline (inspection {inspection.inspection_code})",
                    )
                )
        return entries

    def _annexure_entries(self, case) -> list[TimelineEntry]:
        """One entry per annexure attached to the case (direct document links)."""
        from app.models import Annexure

        kw = self._case_link_kwargs(case)
        entries: list[TimelineEntry] = []
        for annexure in Annexure.query.filter_by(**kw).order_by(Annexure.uploaded_at.asc()).all():
            timestamp = annexure.date or annexure.uploaded_at
            letter = f" {annexure.annexure_letter}" if annexure.annexure_letter else ""
            entries.append(
                TimelineEntry(
                    EVENT_ANNEXURE,
                    timestamp,
                    f"Annexure{letter} uploaded — {annexure.caption}",
                    document_ref=f"annexure:{annexure.id}",
                    document_label=f"Annexure{letter}: {annexure.caption}",
                )
            )
        return entries

    def _evidence_entries(self, case) -> list[TimelineEntry]:
        """One entry per evidence record attached to the case."""
        from app.models import Evidence

        kw = self._case_link_kwargs(case)
        entries: list[TimelineEntry] = []
        for evidence in Evidence.query.filter_by(**kw).order_by(Evidence.uploaded_at.asc()).all():
            timestamp = evidence.captured_at or evidence.uploaded_at
            label = evidence.caption or evidence.filename or evidence.evidence_type
            entries.append(
                TimelineEntry(
                    EVENT_EVIDENCE,
                    timestamp,
                    f"{evidence.evidence_type.replace('_', ' ').title()} — {label}",
                    document_ref=f"evidence:{evidence.id}",
                    document_label=label,
                )
            )
        return entries

    @staticmethod
    def _case_link_kwargs(case) -> dict:
        """Return the ``{case_id: ...}`` / ``{adjudication_id: ...}`` filter for a case."""
        from app.models import CaseFile

        if getattr(case, "id", None) is None:
            return {}
        return {"case_id": case.id} if isinstance(case, CaseFile) else {"adjudication_id": case.id}

    @staticmethod
    def _sort_entries(entries: list[TimelineEntry]) -> list[TimelineEntry]:
        """Sort chronologically (stable — preserves build order on ties)."""
        return sorted(
            (e for e in entries if e.timestamp is not None),
            key=lambda e: e.timestamp,
        )

    # -- Sequence validation ------------------------------------------------ #

    def validate_sequence(self, entries: list[TimelineEntry]) -> list[dict]:
        """Detect chronologically invalid milestone sequences.

        Returns a list of ``{"message", "left", "right"}`` dicts — one per
        detected inversion, e.g. a lab report dated before the sampling date.
        """
        warnings: list[dict] = []

        def ts(event_type: str) -> datetime | None:
            match = next((e for e in entries if e.event_type == event_type), None)
            return match.timestamp if match else None

        def check(earlier: str, later: str, msg: str) -> None:
            a, b = ts(earlier), ts(later)
            if a is not None and b is not None and b < a:
                warnings.append({"message": msg, "left": earlier, "right": later})

        check(
            EVENT_SAMPLING,
            EVENT_LAB_REPORT,
            "Lab report is dated before the sampling date.",
        )
        check(
            EVENT_SAMPLING,
            EVENT_LAB_DISPATCH,
            "Sample dispatch to the lab predates the sampling date.",
        )
        check(
            EVENT_LAB_DISPATCH,
            EVENT_LAB_REPORT,
            "Lab report is dated before the sample was dispatched to the lab.",
        )
        check(
            EVENT_LAB_REPORT,
            EVENT_NOTICE,
            "Directive/notice was issued before the lab report was available.",
        )
        check(
            EVENT_INSPECTION,
            EVENT_SAMPLING,
            "Sampling predates the inspection date.",
        )
        return warnings

    # -- Persistence -------------------------------------------------------- #

    def refresh(self, resolved) -> int:
        """Recompute and persist timeline events for a **case_file** case.

        Idempotent: deletes existing rows for the case, then re-inserts the
        freshly computed milestones in one transaction.  Adjudication cases
        are never persisted (the ``timeline_event.case_id`` FK targets
        ``case_files.id``) — returns 0 for them.

        Returns the number of rows persisted.
        """
        if resolved is None or resolved.case_type != "case_file" or resolved.case_id is None:
            return 0
        return self._persist(resolved.case_id, self.extract(resolved.record))

    @staticmethod
    def _persist(case_id: int, entries: list[TimelineEntry]) -> int:
        """Replace the persisted timeline rows for a case_file (idempotent)."""
        from app.models import TimelineEvent

        try:
            TimelineEvent.query.filter_by(case_id=case_id).delete()
            for entry in entries:
                db.session.add(
                    TimelineEvent(
                        case_id=case_id,
                        case_type="case_file",
                        event_type=entry.event_type,
                        timestamp=entry.timestamp,
                        document_ref=entry.document_ref,
                        description=entry.description,
                    )
                )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("Timeline refresh failed for case %s: %s", case_id, exc)
            raise
        return len(entries)

    # -- Serialization ------------------------------------------------------ #

    def build_payload(self, resolved) -> dict:
        """Build the JSON payload served by the timeline API.

        Persists case_file events (automatic populate), then serializes the
        computed milestones plus any sequence warnings.
        """
        if resolved is None:
            return {"error": "Case not found"}

        entries = self.extract(resolved.record)
        persisted = (
            self._persist(resolved.case_id, entries)
            if resolved.case_type == "case_file" and resolved.case_id is not None
            else 0
        )

        party_name = ""
        record = resolved.record
        if record is not None:
            party_name = (
                getattr(record, "manufacturer_fbo_name", None)
                or getattr(record, "manufacturer_name", None)
                or getattr(record, "fbo_name", None)
                or ""
            )

        return {
            "case_id": resolved.case_id,
            "adjudication_id": resolved.adjudication_id,
            "case_type": resolved.case_type,
            "case_number": resolved.case_number,
            "party_name": party_name,
            "persisted": resolved.case_type == "case_file",
            "persisted_count": persisted,
            "generated_at": datetime.now(UTC).isoformat(),
            "events": [self._serialize(e) for e in entries],
            "warnings": self._deadline_warnings(resolved.record, self.validate_sequence(entries)),
            "deadlines": self._deadline_payload(resolved.record),
        }

    def _deadline_payload(self, record) -> dict:
        """Serialize the computed legal deadlines for the frontend Gantt.

        Always present (values null when the source dates are missing) so
        the UI can render a deadline strip without scanning events.
        """
        now = datetime.now(UTC)
        analyst_date = getattr(record, "analyst_report_date", None) if record is not None else None
        retailer_date = getattr(record, "retailer_report_receive_date", None) if record is not None else None
        manufacturer_date = (
            getattr(record, "manufacturer_report_receive_date", None) if record is not None else None
        )
        authorization_date = getattr(record, "authorization_date", None) if record is not None else None
        deadline = petition_deadline_for(analyst_date)
        gate = generation_gate(retailer_date, manufacturer_date, now=now)
        earliest = gate["earliest"]
        handover = gate["handover"]
        petition_overdue = False
        if deadline is not None:
            try:
                petition_overdue = now.date() > deadline.date()
            except Exception:
                petition_overdue = now > deadline
        return {
            "petition_deadline": deadline.isoformat() if deadline else None,
            "petition_deadline_date": deadline.strftime("%Y-%m-%d") if deadline else None,
            "petition_limit_days": PETITION_LIMIT_DAYS,
            "petition_overdue": petition_overdue,
            "handover_max": handover.isoformat() if handover else None,
            "handover_max_date": handover.strftime("%Y-%m-%d") if handover else None,
            "generation_allowed_from": earliest.isoformat() if earliest else None,
            "generation_allowed_from_date": earliest.strftime("%Y-%m-%d") if earliest else None,
            "embargo_days": APPEAL_EMBARGO_DAYS,
            "generation_blocked": gate["blocked"],
            "authorization_issued": authorization_date is not None,
            "authorization_date": authorization_date.isoformat() if authorization_date else None,
            "is_pre_authorization": str(getattr(record, "pre_authorization", "no") or "no").strip().lower() == "yes",
        }

    def _deadline_warnings(self, record, warnings: list[dict]) -> list[dict]:
        """Append deadline/embargo/authorization notices to the sequence warnings."""
        if record is not None and hasattr(record, "analyst_report_date"):
            payload = self._deadline_payload(record)
            if payload["generation_blocked"] and payload["generation_allowed_from_date"]:
                warnings.append({
                    "message": (
                        "Permission/petition files cannot be generated until "
                        f"{payload['generation_allowed_from_date']} "
                        f"(30 days after report handover on {payload['handover_max_date'] or '—'})."
                    ),
                    "left": "reply",
                    "right": EVENT_GENERATION_ALLOWED,
                })
            if payload["petition_deadline_date"] and payload["petition_overdue"]:
                warnings.append({
                    "message": (
                        "Petition filing deadline has passed "
                        f"({payload['petition_deadline_date']}, 365 days from the analyst report)."
                    ),
                    "left": EVENT_LAB_REPORT,
                    "right": EVENT_PETITION_DEADLINE,
                })
        if record is not None and hasattr(record, "authorization_date"):
            # Pre-authorization adjudications have no petition — never warn.
            is_pre_auth = str(getattr(record, "pre_authorization", "no")).strip().lower() == "yes"
            if not is_pre_auth and not getattr(record, "authorization_date", None):
                warnings.append({
                    "message": (
                        "Authorization pending — the petition cannot be generated until "
                        "the Designated Officer issues authorization (on permission submission)."
                    ),
                    "left": EVENT_NOTICE,
                    "right": EVENT_AUTHORIZATION,
                })
        return warnings

    def _serialize(self, entry: TimelineEntry) -> dict:
        """Serialize a single entry for the JSON API / frontend."""
        label, icon, color = _EVENT_META.get(entry.event_type, (entry.event_type, "fa-circle", "#607d8b"))
        document_url = None
        if entry.document_ref:
            document_url = self._document_url(entry.document_ref)
        return {
            "event_type": entry.event_type,
            "label": label,
            "icon": icon,
            "color": color,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "date": entry.timestamp.strftime("%Y-%m-%d") if entry.timestamp else None,
            "time": entry.timestamp.strftime("%H:%M") if entry.timestamp else None,
            "description": entry.description,
            "document_ref": entry.document_ref,
            "document_label": entry.document_label,
            "document_url": document_url,
        }

    @staticmethod
    def _document_url(document_ref: str) -> str | None:
        """Map a ``"annexure:<id>"`` / ``"evidence:<id>"`` ref to a download URL."""
        try:
            from flask import url_for

            kind, _, ref_id = document_ref.partition(":")
            if kind == "annexure":
                url: str | None = url_for("annexure.download", annexure_id=ref_id)
                return url
            if kind == "evidence":
                url = url_for("evidence.download", evidence_id=ref_id)
                return url
        except Exception:
            logger.warning("Could not build document URL for %s", document_ref)
        return None
