"""Historical-version detection for legal provisions.

Groups chunks/provisions that represent different versions of the same
legal provision into a ``provision_family_id``.  This allows future reranking
to distinguish "currently correct" from "historically correct but repealed".

Version detection strategies (applied in order, no fabrication):

1. **Explicit version field** — if the chunk payload has ``version`` or
   ``provision_version``, use it directly.
2. **Effective date grouping** — provisions with the same Act + section but
   different ``effective_from`` dates are grouped.
3. **Amendment markers** — text patterns like "as amended by", "as modified
   by", "insert after" indicate a versioned relationship.
4. **Neo4j provenance** — best-effort enrichment from the KG's
   ``AMENDS`` / ``REPLACES`` / ``REPEALS`` edges.

All grouping is advisory — when in doubt, each chunk is its own family.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.legal_hierarchy import section_base

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Amendment marker patterns
# --------------------------------------------------------------------------- #

_AMENDMENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "amended_by",
        re.compile(
            r"as\s+(?:amended|modified|substituted|inserted|deleted|omitted)\s+by\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "replaced_by",
        re.compile(
            r"(?:read\s+with|replaced\s+by|substituted\s+by)\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
    ),
    (
        "effective_from",
        re.compile(
            r"(?:effective\s+from|shall\s+come\s+into\s+force\s+on)\s+(\d{4}-\d{2}-\d{2})",
            re.IGNORECASE,
        ),
    ),
    (
        "repealed_by",
        re.compile(
            r"(?:repealed|superseded)\s+by\s+(.+?)(?:\.|$)",
            re.IGNORECASE,
        ),
    ),
]


@dataclass
class ProvisionVersion:
    """A single version of a provision.

    Attributes:
        document_id: The chunk/provision identifier.
        act: Canonical Act name.
        section: Base section number.
        version: Version label (e.g. "2018", "2021-amendment").
        effective_from: When this version became effective.
        effective_to: When this version was superseded/repealed.
        status: current / repealed / suppressed / unknown.
        provision_family_id: ID grouping all versions of this provision.
        is_current: True when this is the latest version still in force.
        evidence: Text snippet supporting the version determination.
    """

    document_id: str | None = None
    act: str | None = None
    section: str | None = None
    version: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: str | None = "unknown"
    provision_family_id: str | None = None
    is_current: bool = False
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "act": self.act,
            "section": self.section,
            "version": self.version,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "status": self.status,
            "provision_family_id": self.provision_family_id,
            "is_current": self.is_current,
            "evidence": self.evidence,
        }


@dataclass
class VersionFamily:
    """A group of versions representing the same legal provision."""

    family_id: str
    act: str | None = None
    section: str | None = None
    versions: list[ProvisionVersion] = field(default_factory=list)

    def current_version(self) -> ProvisionVersion | None:
        """Return the version marked as current, or the latest by effective_from."""
        current = [v for v in self.versions if v.is_current or v.status == "current"]
        if current:
            return current[0]
        # Fall back to latest effective_from
        versioned = sorted(
            self.versions,
            key=lambda v: v.effective_from or "",
            reverse=True,
        )
        return versioned[0] if versioned else None

    def is_current(self, document_id: str) -> bool | None:
        """Check if a specific document_id is the current version."""
        cv = self.current_version()
        if cv is None:
            return None
        return cv.document_id == document_id


# --------------------------------------------------------------------------- #
# Family ID construction
# --------------------------------------------------------------------------- #


def build_provision_family_id(
    act: str | None,
    section: str | None,
    version: str | None = None,
) -> str:
    """Build a stable provision family ID from Act + section.

    Format: ``ACT::SECTION`` (version-independent — all versions share ID).
    Returns ``"UNKNOWN"`` when act and section are both missing.
    """
    if not act and not section:
        return "UNKNOWN"
    parts = []
    if act:
        parts.append(act)
    if section:
        parts.append(section_base(section) or section)
    return "::".join(parts)


# --------------------------------------------------------------------------- #
# Version detection from chunk
# --------------------------------------------------------------------------- #


def _detect_version_from_text(text: str) -> str | None:
    """Try to extract a version marker from chunk text (advisory)."""
    if not text:
        return None
    for _, pattern in _AMENDMENT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return None


def _detect_effective_dates(text: str) -> tuple[str | None, str | None]:
    """Extract effective_from / effective_to from amendment markers.

    Detects dates mentioned near amendment-related keywords (amended,
    repealed, effective from, etc.).
    """
    if not text:
        return None, None

    ef_match = _AMENDMENT_PATTERNS[2][1].search(text)
    et_match = _AMENDMENT_PATTERNS[3][1].search(text)
    ef = ef_match.group(1) if ef_match else None
    et = et_match.group(1) if et_match else None

    # Also detect dates near amendment keywords like "amended by ... on 2021-01-15"
    if not ef:
        amend_pattern = _AMENDMENT_PATTERNS[0][1]
        m = amend_pattern.search(text)
        if m:
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})", m.group(0))
            if date_m:
                ef = date_m.group(1)

    return ef, et


def extract_provision_version(
    chunk: Any,
    kg_versions: list[dict[str, Any]] | None = None,
) -> ProvisionVersion:
    """Extract version information from a chunk.

    Args:
        chunk: ``RetrievedChunk`` or chunk-like dict.
        kg_versions: Optional list of version dicts from Neo4j
            (for graph-enriched versions).

    Returns:
        ``ProvisionVersion`` with all detectable fields populated.
    """

    # Resolve chunk attributes (support both objects and dicts)
    def _get(attr: str, default: str | None = None) -> str | None:
        val = getattr(chunk, attr, None)
        if val is None and isinstance(chunk, dict):
            val = chunk.get(attr)
        if val is None:
            return default
        return str(val) if val else default

    document_id = _get("chunk_id") or _get("document_id")
    act = _get("act_name") or _get("act")
    section = _get("section_number")
    text = _get("text", "") or ""
    status = _get("status", "unknown") or "unknown"
    effective_from = _get("effective_from")
    effective_to = _get("effective_to")
    version = _get("version") or _get("provision_version")

    # If no section_number field, try text-level extraction
    if not section and text:
        try:
            from app.rag.retrieval.identifier import detect_section

            sec, _ = detect_section(text)
            if sec:
                section = sec
        except Exception:
            pass

    # If no explicit version, try text-level detection (advisory)
    text_version = _detect_version_from_text(text) if text else None
    text_ef, text_et = _detect_effective_dates(text) if text else (None, None)

    if not version:
        version = text_version
    if not effective_from:
        effective_from = text_ef
    if not effective_to:
        effective_to = text_et

    family_id = build_provision_family_id(act, section, version)

    is_current = status.lower() in ("current", "active", "in_force")

    evidence = ""
    if text_version:
        evidence = text_version
    elif version:
        evidence = f"version={version}"

    return ProvisionVersion(
        document_id=document_id,
        act=act,
        section=section_base(section) if section else None,
        version=version,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        provision_family_id=family_id,
        is_current=is_current,
        evidence=evidence,
    )


def group_versions(chunks: list[Any]) -> dict[str, VersionFamily]:
    """Group chunks into version families.

    Args:
        chunks: List of ``RetrievedChunk`` objects.

    Returns:
        Dict mapping ``family_id`` → ``VersionFamily``.
    """
    families: dict[str, VersionFamily] = {}
    for chunk in chunks:
        version_info = extract_provision_version(chunk)
        family_id = version_info.provision_family_id or "UNKNOWN"
        if family_id not in families:
            families[family_id] = VersionFamily(
                family_id=family_id,
                act=version_info.act,
                section=version_info.section,
            )
        families[family_id].versions.append(version_info)
    return families


def is_current_version(
    chunk: Any,
    family: VersionFamily | None = None,
) -> bool | None:
    """Check if a chunk is the current version of its provision.

    Args:
        chunk: The chunk to check.
        family: Optional pre-computed VersionFamily.

    Returns:
        ``True`` if current, ``False`` if historical, ``None`` if ambiguous.
    """
    if family is None:
        families = group_versions([chunk])
        family = next(iter(families.values()), None)
    if family is None or not family.versions:
        return None
    return family.is_current(getattr(chunk, "chunk_id", None) or getattr(chunk, "document_id", None))


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    from dataclasses import dataclass

    @dataclass
    class FakeChunk:
        chunk_id: str
        text: str
        act_name: str
        section_number: str
        status: str
        effective_from: str | None = None
        effective_to: str | None = None
        version: str | None = None

    c1 = FakeChunk(
        chunk_id="c1",
        text="Section 31 of FSS Act as amended by Rule 5",
        act_name="Food Safety and Standards Act, 2006",
        section_number="31",
        status="current",
        effective_from="2020-01-01",
    )
    c2 = FakeChunk(
        chunk_id="c2",
        text="Section 31 of FSS Act (repealed by 2023 Act)",
        act_name="Food Safety and Standards Act, 2006",
        section_number="31",
        status="repealed",
        effective_to="2023-06-01",
    )

    families = group_versions([c1, c2])
    assert len(families) == 1, f"Expected 1 family, got {len(families)}"
    fam = next(iter(families.values()))
    assert len(fam.versions) == 2
    assert fam.is_current("c1") is True
    assert fam.is_current("c2") is False

    v = extract_provision_version(c1)
    assert v.is_current is True
    assert v.effective_from == "2020-01-01"
