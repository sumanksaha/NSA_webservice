"""Generation access — one home for "may this document generate?".

Ten call sites used to sequence up to three checks (30-day embargo,
authorization date, pre-auth routing) by hand; omission order caused
403/200 mismatches. This seam answers per document type in one fixed
order:

  1. embargo — case_file track only (current behavior; the handover
     embargo is a sample-track concept),
  2. authorization — petition-class documents, unless the caller opts
     into the pre-authorization draft exception (regeneration renders a
     draft bundle before the Designated Officer issues the date),
  3. allowed.

Pre-auth "no petition" 400s stay at the routes — that is routing ("this
download does not exist for pre-auth cases"), not a temporal gate.

The module is transport-free: it returns :class:`GateDecision` (pure
data); routes render ``jsonify(decision.payload()), decision.status``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.shared.authorization_gate import AUTHORIZATION_REQUIRED_ERROR
from app.utils.filters import format_date_indian

_PETITION_CLASS = ("petition", "both")


@dataclass(frozen=True)
class GateDecision:
    """Pure verdict of one generation-access check."""

    allowed: bool
    error: str | None = None
    status: int | None = None
    earliest_allowed_date: str | None = None
    handover_date: str | None = None
    extra: dict = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """The JSON body the routes render for a denied decision."""
        body: dict[str, Any] = {"error": self.error}
        if self.earliest_allowed_date is not None:
            body["earliest_allowed_date"] = self.earliest_allowed_date
        if self.handover_date is not None:
            body["handover_date"] = self.handover_date
        body.update(self.extra)
        return body


def check_generation_allowed(
    *,
    case_type: str,
    doc_type: str,
    authorization_date: Any = None,
    retailer_receive: Any = None,
    manufacturer_receive: Any = None,
    require_authorization: bool = True,
) -> GateDecision:
    """Evaluate embargo then authorization for one document render."""
    from app.shared.authorization_gate import _normalize as _normalize_auth
    from app.timeline.engine import generation_gate

    if case_type == "case_file":
        gate = generation_gate(retailer_receive, manufacturer_receive)
        if gate["blocked"]:
            earliest = gate["earliest"]
            handover = gate["handover"]
            earliest_str = format_date_indian(earliest) if earliest else "—"
            handover_str = format_date_indian(handover) if handover else "—"
            return GateDecision(
                allowed=False,
                error=(
                    "Permission/petition files cannot be generated until "
                    f"{earliest_str} (30 days after report handover on {handover_str})."
                ),
                status=403,
                earliest_allowed_date=earliest.isoformat() if earliest else None,
                handover_date=handover.isoformat() if handover else None,
            )

    if (
        doc_type in _PETITION_CLASS
        and require_authorization
        and _normalize_auth(authorization_date) is None
    ):
        return GateDecision(allowed=False, error=AUTHORIZATION_REQUIRED_ERROR, status=403)

    return GateDecision(allowed=True)
