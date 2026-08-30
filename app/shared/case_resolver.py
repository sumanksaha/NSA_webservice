import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ResolvedCase:
    case_id: int | None = None
    case_type: str | None = None
    case_number: str | None = None
    adjudication_id: int | None = None
    record: object | None = None  # CaseFile or Adjudication instance


class CaseResolver:
    """Seam: resolves a case ID to CaseFile or Adjudication."""

    def resolve(self, case_id, kind=None) -> ResolvedCase | None:
        from app.extensions import db
        from app.models import Adjudication, CaseFile

        # Try case_file first (or honor kind)
        try:
            if kind is None or kind == "case_file":
                case = db.session.get(CaseFile, case_id)
                if case:
                    return ResolvedCase(
                        case_id=case.id,
                        case_type="case_file",
                        case_number=case.case_number,
                        adjudication_id=None,
                        record=case,
                    )
            if kind is None or kind == "adjudication":
                case = db.session.get(Adjudication, case_id)
                if case:
                    return ResolvedCase(
                        case_id=None,
                        case_type="adjudication",
                        case_number=case.case_number,
                        adjudication_id=case.id,
                        record=case,
                    )
        except Exception as exc:
            logger.warning("CaseResolver failed for id=%s kind=%s: %s", case_id, kind, exc)
        return None
