"""Photo selection for documents — one home for "which photos go in".

Seven copies of this logic lived behind route handlers (some coupled to
``request.args``, some silently PASS-only, all OR-ing both id columns
against a single id so a case and an adjudication sharing an integer
could leak each other's photos). Callers pass explicit ids and flags —
no request context, no filesystem (the embedder is injectable).

Flagged-photo policy: verified photos always go in; FLAG photos only
with an explicit reason, which is audit-logged. Missing reason raises
:exc:`FlagReasonRequired` (a ``ValueError``); transport layers map it to
their 400 contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PhotoSelection:
    """Selected photos plus their base64 embeds for template rendering."""

    photos: list = field(default_factory=list)
    embeds: list = field(default_factory=list)
    flagged_included: bool = False


class FlagReasonRequired(ValueError):
    """Raised when flagged photos are requested without a reason."""


def select_for_document(
    *,
    case_id: int | None = None,
    adjudication_id: int | None = None,
    include_flagged: bool = False,
    flag_reason: str = "",
    actor: str = "unknown",
    embedder: Callable[[list], list] | None = None,
    auditor: Any | None = None,
) -> PhotoSelection:
    """Select and embed evidence photos for one document render."""
    from app.models import Evidence

    if (case_id is None) == (adjudication_id is None):
        raise ValueError("select_for_document requires exactly one of case_id / adjudication_id")

    criterion = Evidence.case_id == case_id if case_id is not None else Evidence.adjudication_id == adjudication_id
    all_photos = (
        Evidence.query
        .filter(Evidence.evidence_type == "photo", criterion)
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    verified = [p for p in all_photos if p.verification_status == "PASS"]
    flagged = [p for p in all_photos if p.verification_status == "FLAG"]

    flagged_included = False
    if include_flagged:
        reason = (flag_reason or "").strip()
        if not reason:
            raise FlagReasonRequired("flag_override_reason is required when include_flagged=true")
        flagged_included = True
        flagged_ids = [p.id for p in flagged]
        if flagged_ids:
            if auditor is None:
                from app.services.audit_context import audit_logger

                auditor = audit_logger("photo")
            auditor.log(",".join(flagged_ids), "FLAGGED_PHOTO_INCLUDED", actor=actor, reason=reason)
        final_photos = verified + flagged
    else:
        final_photos = verified

    resolve_embedder = embedder
    if resolve_embedder is None:
        from app.utils.pdf_utils import embed_photos_as_base64

        resolve_embedder = embed_photos_as_base64
    return PhotoSelection(
        photos=final_photos,
        embeds=resolve_embedder([p.filepath for p in final_photos]),
        flagged_included=flagged_included,
    )
