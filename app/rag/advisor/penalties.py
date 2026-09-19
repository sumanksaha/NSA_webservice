"""Statutory penalties and anchors under the Food Safety and Standards Act, 2006.

Fine / imprisonment values are grounded in ``fss_sections.md`` (§51, §52,
§55, §56, §58, §63, §64). §32 (Improvement Notice) is real Act text but has
no penalty schedule — it is a binding notice, not a fine — so it anchors
with zero fine / zero imprisonment and may be emitted via the prior-notice
rule even when no §32 chunk was retrieved.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatutoryAnchor:
    """One grounded FSS Act section with its maximum statutory consequence."""

    section: str
    title: str
    max_fine_inr: int
    imprisonment_months: int
    is_cognizable: bool
    requires_prior_notice: bool


FSSAI_PENALTY_SCHEDULE: dict[str, StatutoryAnchor] = {
    "32": StatutoryAnchor("32", "Improvement Notice", 0, 0, False, False),
    "51": StatutoryAnchor("51", "Penalty for sub-standard food", 500_000, 0, False, False),
    "52": StatutoryAnchor("52", "Penalty for misbranded food", 300_000, 0, False, False),
    "55": StatutoryAnchor("55", "Failure to comply with FSO directions", 200_000, 0, False, True),
    "56": StatutoryAnchor("56", "Unhygienic/unsanitary processing", 100_000, 0, False, False),
    "58": StatutoryAnchor("58", "Contravention without specific penalty", 200_000, 0, False, False),
    "63": StatutoryAnchor("63", "Carrying out business without licence", 500_000, 6, True, False),
    "64": StatutoryAnchor("64", "Punishment for subsequent offences", 1_000_000, 12, True, False),
}
