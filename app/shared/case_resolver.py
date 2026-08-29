from dataclasses import dataclass


@dataclass
class ResolvedCase:
    case_file: object | None  # CaseFile instance
    adjudication: object | None  # Adjudication instance
    kind: str | None


class CaseResolver:
    """Seam: resolves a case ID to CaseFile or Adjudication.
    Interface is thin; depth lives in the adapter (DB query)."""

    def resolve(self, case_id, kind=None) -> ResolvedCase | None:
        # Behind the seam: tries both tables
        return ResolvedCase(case_file=None, adjudication=None, kind=kind)
