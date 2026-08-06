"""Rule definitions for the Phase 12 Legal Validation Engine (``app/validation/``).

Each rule is a pure function of a plain-dict ``case_data`` payload assembled
by :class:`app.validation.engine.ValidationEngine` — rules never touch the
database directly, which keeps them trivially unit-testable:

    case_data = {
        "case_id": int | None,
        "adjudication_id": int | None,
        "case_type": "case_file" | "adjudication",
        "case_number": str,
        "fields": {...},               # case_file_to_dict / adjudication_to_dict output
        "annexures": [ {...}, ... ],   # serialized annexure rows
        "evidence": [ {...}, ... ],    # serialized evidence rows
        "sample": {...} | None,        # linked sample (case_file only)
        "document_html": str,          # rendered petition HTML ("" when render failed)
        "document_html_permission": str,  # rendered permission-letter HTML
        "suggested_sections": {"sections": [...], "reasoning": {...}},
    }

Severities: ``ERROR`` (blocks a clean bill of health, -15 pts), ``WARNING``
(needs attention, -5 pts), ``INFO`` (advisory, no score impact).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import pairwise
from typing import ClassVar

from app.utils.sections_data import SECTIONS, VALID_SECTION_IDS

#: Canonical code format: uppercase alphanumerics, dashes, slashes.
_NUMBER_RE = re.compile(r"^[A-Z0-9/\\-]+$")

#: Substring markers that indicate a signature placeholder is present in a
#: rendered document.  The document templates use real signature blocks
#: (``Signature of Food Safety Officer: ___``, ``class="signature"``, ...)
#: rather than ``{{ signature }}`` placeholders, so the check looks for
#: those rendered markers.
_SIGNATURE_MARKERS = (
    "signature of the food safety officer",
    "signature of designated officer",
    "signature of the complainant",
    "signature :",
    "signature:",
    'class="signature',
    "signature-section",
)

#: Sections the officer ticks manually and therefore need no checklist support.
_MANUAL_ONLY_SECTIONS = frozenset({"58", "64"})

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"


@dataclass
class ValidationResult:
    """A single finding produced by a rule."""

    rule_id: str
    severity: str  # 'ERROR' | 'WARNING' | 'INFO'
    message: str
    field_name: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class BaseRule(ABC):
    """Abstract base class for a single validation rule."""

    rule_id: str = "base"
    description: str = ""

    @abstractmethod
    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        """Run the rule against a ``case_data`` payload; return findings."""


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _as_datetime(value):
    """Normalize a datetime / ISO-8601 string / None to a tz-naive datetime.

    SQLite returns naive datetimes and the dict serializers emit ISO strings;
    both are normalized here so comparisons never mix aware/naive values.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _split_sections(raw) -> list[str]:
    """Split ``"55, 56"`` / ``"55 and 56"`` / ``["55"]`` into clean section ids."""
    if raw is None:
        return []
    parts = (
        raw
        if isinstance(raw, (list, tuple))
        else re.split(r"\band\b|[^0-9A-Z]+", str(raw), flags=re.IGNORECASE)
    )
    return [str(int(part)) for part in parts if part.strip().isdigit()]


def _is_yes(value) -> bool:
    return str(value or "").strip().lower() == "yes"


# --------------------------------------------------------------------------- #
# Concrete rules
# --------------------------------------------------------------------------- #


class MandatorySectionsRule(BaseRule):
    """Required section presence — at least one applicable FSS Act section."""

    rule_id = "mandatory_sections"
    description = "Checks that the case records applicable FSS Act sections."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        fields = case_data.get("fields") or {}
        results: list[ValidationResult] = []

        if case_data.get("case_type") == "adjudication":
            selected = [
                section
                for section in ("55", "56", "58", "63", "64")
                if _is_yes(fields.get(f"section_{section}"))
            ]
            if not selected:
                results.append(
                    ValidationResult(
                        self.rule_id,
                        ERROR,
                        "No FSS Act section is selected for this adjudication "
                        "(sections 55, 56, 58, 63, 64 are all unchecked).",
                        field_name="section_55",
                        suggestion="Tick at least one applicable section — use the "
                        "'Suggest sections' helper on the adjudication form.",
                    )
                )
        else:
            cited = _split_sections(fields.get("applicable_sections"))
            if not cited:
                results.append(
                    ValidationResult(
                        self.rule_id,
                        ERROR,
                        "No applicable FSS Act section is recorded for this case file.",
                        field_name="applicable_sections",
                        suggestion="Record the analysis result (substandard / misbranded) "
                        "so the applicable sections are derived.",
                    )
                )
        return results


class SignaturePlaceholderRule(BaseRule):
    """Generated documents must carry a signature placeholder."""

    rule_id = "signature_placeholder"
    description = "Verifies the generated petition / permission letter contain a signature block."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        html = (case_data.get("document_html") or "").lower()
        permission = (case_data.get("document_html_permission") or "").lower()

        if not html and not permission:
            return [
                ValidationResult(
                    self.rule_id,
                    INFO,
                    "Documents could not be rendered — signature check skipped.",
                )
            ]

        has_signature = any(marker in html for marker in _SIGNATURE_MARKERS) or any(
            marker in permission for marker in _SIGNATURE_MARKERS
        )
        if not has_signature:
            return [
                ValidationResult(
                    self.rule_id,
                    ERROR,
                    "No signature placeholder found in the generated petition or permission letter.",
                    field_name="document",
                    suggestion="Ensure the document template includes a signature block "
                    "(e.g. 'Signature of Food Safety Officer: ______').",
                )
            ]
        return []


class NumberingFormatRule(BaseRule):
    """Reference codes must match the canonical ``^[A-Z0-9/-]+$`` format."""

    rule_id = "numbering_format"
    description = "Validates case / sample / lab reference codes against the canonical format."

    _EXAMPLE: ClassVar[dict] = {
        "case_number": "CF/2026/001",
        "sample_code": "SMP-2026-001",
        "lab_registration_no": "LAB/2026/0001",
        "fssai_license": "11523998000432",
        "ce_license_no": "CE/1234/2025",
    }

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        fields = case_data.get("fields") or {}
        if case_data.get("case_type") == "case_file":
            checks = [
                ("case_number", ERROR),
                ("sample_code", ERROR),
                ("lab_registration_no", ERROR),
            ]
        else:
            checks = [
                ("case_number", ERROR),
                ("fssai_license", WARNING),
                ("ce_license_no", WARNING),
            ]

        results: list[ValidationResult] = []
        for field_name, severity in checks:
            value = fields.get(field_name)
            if value is None or not str(value).strip():
                continue
            if not _NUMBER_RE.match(str(value).strip()):
                results.append(
                    ValidationResult(
                        self.rule_id,
                        severity,
                        f"{field_name.replace('_', ' ').title()} '{value}' contains "
                        "characters outside the allowed A-Z / 0-9 / - / / set.",
                        field_name=field_name,
                        suggestion=f"Use only uppercase letters, digits, dashes and slashes "
                        f"(e.g. '{self._EXAMPLE.get(field_name, 'A1/B2')}').",
                    )
                )
        return results


class StatutoryReferenceRule(BaseRule):
    """Cited FSSA 2006 sections must be valid and supported by checklist evidence."""

    rule_id = "statutory_reference"
    description = "Validates FSSA 2006 section citations against the tracked set and checklist."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        fields = case_data.get("fields") or {}
        case_type = case_data.get("case_type")
        results: list[ValidationResult] = []

        if case_type == "adjudication":
            selected = {
                section
                for section in ("55", "56", "58", "63", "64")
                if _is_yes(fields.get(f"section_{section}"))
            }
            suggested_info = case_data.get("suggested_sections") or {}
            suggested = set(suggested_info.get("sections") or [])
            reasoning = suggested_info.get("reasoning") or {}

            # Checklist evidence points at sections the officer did not select.
            for section in sorted(suggested - selected):
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        f"Checklist evidence suggests Section {section}, but it is not "
                        "selected on the form.",
                        field_name=f"section_{section}",
                        suggestion=reasoning.get(section),
                    )
                )
            # Selected sections with no checklist support (manual-only exempt).
            for section in sorted(selected - suggested - _MANUAL_ONLY_SECTIONS):
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        f"Section {section} is selected but no checklist item supports it.",
                        field_name=f"section_{section}",
                        suggestion="Confirm the violation is evidenced in the inspection checklist.",
                    )
                )
        else:
            cited = set(_split_sections(fields.get("applicable_sections")))
            for section in sorted(cited):
                if section not in VALID_SECTION_IDS:
                    results.append(
                        ValidationResult(
                            self.rule_id,
                            WARNING,
                            f"Section {section} is cited but is not among the tracked FSS Act "
                            "sections (55, 56, 58, 63, 64).",
                            field_name="applicable_sections",
                            suggestion="Verify the section citation against the FSS Act, 2006.",
                        )
                    )

        # Every cited section must have statutory text available on file.
        all_cited = _split_sections(fields.get("applicable_sections"))
        if case_type == "adjudication":
            all_cited = [
                section
                for section in ("55", "56", "58", "63", "64")
                if _is_yes(fields.get(f"section_{section}"))
            ]
        for section in sorted(set(all_cited)):
            if section not in SECTIONS:
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        f"No statutory text is on file for Section {section}.",
                        field_name="applicable_sections",
                        suggestion="Add the section text to fss_sections.md.",
                    )
                )
        return results


class DuplicateEvidenceRule(BaseRule):
    """Duplicate evidence detection via SHA-256 content hashes."""

    rule_id = "duplicate_evidence"
    description = "Flags evidence records that share a SHA-256 content hash."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        by_hash: dict[str, list[dict]] = {}
        for item in case_data.get("evidence") or []:
            digest = item.get("file_hash")
            if digest:
                by_hash.setdefault(digest, []).append(item)

        results: list[ValidationResult] = []
        for digest, items in by_hash.items():
            if len(items) > 1:
                names = ", ".join(
                    str(i.get("filename") or i.get("caption") or i.get("id")) for i in items
                )
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        f"{len(items)} evidence records share the same content hash "
                        f"({digest[:12]}…) — possible duplicate upload.",
                        field_name="file_hash",
                        suggestion=f"Files: {names}. Keep one record and delete the duplicates.",
                    )
                )
        return results


class TimelineConsistencyRule(BaseRule):
    """Legal-proceeding dates must run in chronological order."""

    rule_id = "timeline_consistency"
    description = "Asserts the legal-proceeding date sequence is chronologically valid."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        fields = case_data.get("fields") or {}
        if case_data.get("case_type") == "adjudication":
            chain = [
                ("complaint_date", "Complaint"),
                ("first_inspection_date", "First inspection"),
                ("followup_inspection_date", "Follow-up inspection"),
                ("compliance_deadline", "Compliance deadline"),
            ]
        else:
            chain = [
                ("sample_submission_date", "Sample submission"),
                ("do_receipt_date", "Lab receipt (DO)"),
                ("analyst_report_date", "Analyst report"),
            ]

        present = [
            (field, label, dt)
            for field, label in chain
            if (dt := _as_datetime(fields.get(field))) is not None
        ]

        results: list[ValidationResult] = []
        if not present:
            return [
                ValidationResult(
                    self.rule_id,
                    INFO,
                    "No key dates are recorded — timeline consistency cannot be checked.",
                )
            ]

        if len(present) < len(chain):
            results.append(
                ValidationResult(
                    self.rule_id,
                    INFO,
                    f"Timeline is incomplete ({len(present)} of {len(chain)} key dates recorded).",
                )
            )

        for (_left_field, left_label, left_dt), (right_field, right_label, right_dt) in pairwise(
            present
        ):
            if left_dt > right_dt:
                results.append(
                    ValidationResult(
                        self.rule_id,
                        ERROR,
                        f"{right_label} ({right_dt.date().isoformat()}) is dated before "
                        f"{left_label} ({left_dt.date().isoformat()}).",
                        field_name=right_field,
                        suggestion="Correct the dates so the proceeding steps run in chronological order.",
                    )
                )
        return results


class DocumentCompletenessRule(BaseRule):
    """Unlinked annexures, empty captions, and missing evidence metadata."""

    rule_id = "document_completeness"
    description = "Scans for unlinked annexures, incomplete captions, and missing evidence metadata."

    def evaluate(self, case_data: dict) -> list[ValidationResult]:
        results: list[ValidationResult] = []

        for annexure in case_data.get("annexures") or []:
            label = annexure.get("caption") or annexure.get("filename") or annexure.get("id")
            if not annexure.get("case_id") and not annexure.get("adjudication_id"):
                results.append(
                    ValidationResult(
                        self.rule_id,
                        ERROR,
                        f"Annexure '{label}' is not linked to any case.",
                        field_name="annexure",
                        suggestion="Attach the annexure to its case file or adjudication.",
                    )
                )
            if not str(annexure.get("caption") or "").strip():
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        "An annexure has an empty caption.",
                        field_name="caption",
                        suggestion="Give every annexure a descriptive caption.",
                    )
                )

        evidence = case_data.get("evidence") or []
        if not evidence:
            results.append(
                ValidationResult(
                    self.rule_id,
                    INFO,
                    "No evidence records are attached to this case.",
                    suggestion="Attach photos, lab reports, or other supporting evidence.",
                )
            )
        for item in evidence:
            if not item.get("file_hash"):
                results.append(
                    ValidationResult(
                        self.rule_id,
                        WARNING,
                        f"Evidence '{item.get('filename') or item.get('id')}' is missing "
                        "its content hash.",
                        field_name="file_hash",
                        suggestion="Re-upload the file so a SHA-256 hash can be recorded.",
                    )
                )
        return results


# --------------------------------------------------------------------------- #
# Registry — order matters for deterministic output (runs in listed order)
# --------------------------------------------------------------------------- #

RULES: list[type[BaseRule]] = [
    MandatorySectionsRule,
    SignaturePlaceholderRule,
    NumberingFormatRule,
    StatutoryReferenceRule,
    DuplicateEvidenceRule,
    TimelineConsistencyRule,
    DocumentCompletenessRule,
]

__all__ = [
    "ERROR",
    "INFO",
    "RULES",
    "WARNING",
    "BaseRule",
    "DocumentCompletenessRule",
    "DuplicateEvidenceRule",
    "MandatorySectionsRule",
    "NumberingFormatRule",
    "SignaturePlaceholderRule",
    "StatutoryReferenceRule",
    "TimelineConsistencyRule",
    "ValidationResult",
]
