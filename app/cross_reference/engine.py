"""Automatic cross-reference engine for legal documents (Phase 6).

Scans petition/permission document text for cross-references — paragraph
numbers, Annexure references, and FSS Act Section references — links them
to stored annexure metadata (letter, caption, page count), and provides
renumbering passes that keep paragraph numbering and ``<ol start=...>``
continuation lists correct after paragraphs are inserted or deleted.

Design notes:
- Extraction and text/HTML renumbering are pure (no DB) and thread-safe.
- Annexure linking and letter renumbering lazily import the ORM so the
  module stays importable outside an app context.
- ``annotate_html()`` is the PDF-assembly entry point: it renumbers lists
  and, when a ``<ol data-cross-reference="enclosures">`` placeholder is
  present, fills it with the auto-generated "List of Enclosures".
"""

from __future__ import annotations

import html as _html
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# FSS Act sections referenced by the app's documents / suggester rules.
KNOWN_SECTIONS = frozenset({"3", "26", "37", "46", "51", "52", "55", "56", "58", "63", "64"})

_SECTION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# --- Reference extraction patterns -------------------------------------------------
# Annexure refs: "Annexure A", "Annexure-A", "Annexure 1", "Annexure No. B"
_ANNEXURE_RE = re.compile(
    r"\bAnnexure(?!s)\b\s*[-:\u2013\u2014.]?\s*(?:No\.?\s*)?\(?\s*([A-Za-z]|\d{1,2})\s*\)?",
    re.IGNORECASE,
)
# Section runs: "Section 55", "Sections 55, 56 and 58", "u/s 63", "Sec. 51"
_SECTION_RUN_RE = re.compile(
    r"\b(?:Section|Sec\.?|Sections|u/s)\s+(\d{1,3}(?:\s*[,&and-]+\s*\d{1,3})*)",
    re.IGNORECASE,
)
# Sub-clause section refs: "Section 26(2)(ii)", "s.26(2)", "u/s 55(1)"
_SECTION_SUBCLAUSE_RE = re.compile(
    r"\b(?:Section|Sec\.?|s\.?|u/s)\s*(\d{1,3}(?:\(\d+\)|\([a-z]+\)){1,3})",
    re.IGNORECASE,
)
# Paragraph word refs: "paragraph 3", "para 5", "clause 2"
_PARA_WORD_RE = re.compile(r"\b(?:paragraph|para\.?|clause)\s+(\d{1,3})", re.IGNORECASE)
# Numbered list markers at line starts: "1. ...", "(1) ..."
_LIST_MARKER_RE = re.compile(
    r"(?m)^(?P<lead>\s*)(?:\((?P<paren>\d+)\)|(?P<dot>\d+)\.|(?P<letter>[A-Za-z])\.)(?P<sep>\s+)(?=\S)"
)

# --- HTML renumbering patterns ------------------------------------------------------
_OL_BLOCK_RE = re.compile(r"<ol\b([^>]*)>(.*?)</ol>", re.IGNORECASE | re.DOTALL)
_START_ATTR_RE = re.compile(r"(start\s*=\s*[\"']?)(\d+)([\"']?)", re.IGNORECASE)
_NESTED_OL_RE = re.compile(r"<ol\b", re.IGNORECASE)
_ENCLOSURES_PLACEHOLDER_RE = re.compile(
    r"<ol\b[^>]*data-cross-reference=[\"']enclosures[\"'][^>]*>\s*</ol>",
    re.IGNORECASE,
)


class ReferenceKind(StrEnum):
    """Kind of a detected cross-reference."""

    PARAGRAPH = "paragraph"
    ANNEXURE = "annexure"
    SECTION = "section"


@dataclass
class CrossReference:
    """A single detected cross-reference in a document."""

    kind: ReferenceKind
    target: str
    raw: str
    position: int
    context: str = ""
    confidence: float = 0.0
    resolved: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "raw": self.raw,
            "position": self.position,
            "context": self.context,
            "confidence": self.confidence,
            "resolved": self.resolved,
        }


def _context_around(text: str, start: int, end: int, window: int = 60) -> str:
    """Return a trimmed context snippet around a match."""
    left = text[max(0, start - window) : start]
    right = text[end : end + window]
    return f"{left}[{text[start:end]}]{right}".replace("\n", " ")


def _esc(value: str) -> str:
    return _html.escape(str(value), quote=True)


class CrossReferenceEngine:
    """Extract, link, and renumber cross-references in legal documents."""

    # ------------------------------------------------------------------
    # Extraction (pure)
    # ------------------------------------------------------------------
    def extract_references(self, text: str) -> list[CrossReference]:
        """Return all detected references, deduplicated by position."""
        if not text:
            return []
        refs: list[CrossReference] = []
        refs.extend(self._extract_annexures(text))
        refs.extend(self._extract_sections(text))
        refs.extend(self._extract_paragraphs(text))
        return self._dedupe(refs)

    def _extract_annexures(self, text: str) -> list[CrossReference]:
        refs = []
        for match in _ANNEXURE_RE.finditer(text):
            target = match.group(1).upper() if match.group(1).isalpha() else match.group(1)
            refs.append(
                CrossReference(
                    kind=ReferenceKind.ANNEXURE,
                    target=target,
                    raw=match.group(0).strip(),
                    position=match.start(),
                    context=_context_around(text, match.start(), match.end()),
                    confidence=0.9,
                )
            )
        return refs

    def _extract_sections(self, text: str) -> list[CrossReference]:
        refs: list[CrossReference] = []
        # Plural/simple runs: "Sections 55, 56 and 58" → one ref per number.
        for match in _SECTION_RUN_RE.finditer(text):
            numbers = re.split(r"\s*[,&and-]+\s*", match.group(1).strip())
            for number in numbers:
                number = number.strip()
                if not number.isdigit():
                    continue
                refs.append(
                    CrossReference(
                        kind=ReferenceKind.SECTION,
                        target=number,
                        raw=f"Section {number}",
                        position=match.start(),
                        context=_context_around(text, match.start(), match.end()),
                        confidence=0.9,
                    )
                )
        # Sub-clause refs: "Section 26(2)(ii)" (deduped against the run refs).
        for match in _SECTION_SUBCLAUSE_RE.finditer(text):
            target = match.group(1)
            if any(r.position == match.start() and r.target == target for r in refs):
                continue
            refs.append(
                CrossReference(
                    kind=ReferenceKind.SECTION,
                    target=target,
                    raw=match.group(0).strip(),
                    position=match.start(),
                    context=_context_around(text, match.start(), match.end()),
                    confidence=0.85,
                )
            )
        return refs

    def _extract_paragraphs(self, text: str) -> list[CrossReference]:
        refs: list[CrossReference] = []
        for match in _PARA_WORD_RE.finditer(text):
            refs.append(
                CrossReference(
                    kind=ReferenceKind.PARAGRAPH,
                    target=match.group(1),
                    raw=match.group(0).strip(),
                    position=match.start(),
                    context=_context_around(text, match.start(), match.end()),
                    confidence=0.8,
                )
            )
        # Numbered list markers — each is a paragraph reference.
        for match in _LIST_MARKER_RE.finditer(text):
            number = match.group("paren") or match.group("dot")
            if number is None:
                continue
            refs.append(
                CrossReference(
                    kind=ReferenceKind.PARAGRAPH,
                    target=number,
                    raw=match.group(0).strip(),
                    position=match.start(),
                    context=_context_around(text, match.start(), match.end()),
                    confidence=0.7,
                )
            )
        return refs

    @staticmethod
    def _dedupe(refs: list[CrossReference]) -> list[CrossReference]:
        seen: set[tuple[Any, int, str]] = set()
        out: list[CrossReference] = []
        for ref in sorted(refs, key=lambda r: (r.position, r.kind.value)):
            key = (ref.kind, ref.position, ref.raw)
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
        return out

    # ------------------------------------------------------------------
    # Annexure linking (DB-backed, lazy imports)
    # ------------------------------------------------------------------
    def _load_annexures(self, case_id: int | None, adjudication_id: int | None) -> list[Any]:
        """Load annexures attached to a case or adjudication, in letter order."""
        from sqlalchemy import or_

        from app.models import Annexure

        filters = []
        if case_id:
            filters.append(Annexure.case_id == case_id)
        if adjudication_id:
            filters.append(Annexure.adjudication_id == adjudication_id)
        if not filters:
            return []
        return list(Annexure.query.filter(or_(*filters)).order_by(Annexure.uploaded_at.asc(), Annexure.id.asc()).all())

    def _annexure_meta(self, annexure: Any) -> dict[str, Any]:
        return {
            "annexure_id": annexure.id,
            "annexure_letter": annexure.annexure_letter,
            "caption": annexure.caption,
            "filename": annexure.filename,
            "page_count": annexure.page_count,
        }

    def link_references(
        self,
        text: str,
        case_id: int | None = None,
        adjudication_id: int | None = None,
    ) -> dict[str, Any]:
        """Extract references and resolve them against annexure metadata.

        Returns a JSON-safe report: ``{"references", "resolved",
        "unresolved", "annexures", "sections_known"}``. Annexure refs are
        matched by letter (A-Z) or by 1-based index into the letter-sorted
        annexure list.
        """
        refs = self.extract_references(text)
        annexures = self._load_annexures(case_id, adjudication_id)
        by_letter = {a.annexure_letter: a for a in annexures if a.annexure_letter}

        resolved: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for ref in refs:
            if ref.kind is ReferenceKind.ANNEXURE:
                matched = None
                if ref.target.isalpha():
                    matched = by_letter.get(ref.target)
                else:
                    try:
                        idx = int(ref.target) - 1
                        if 0 <= idx < len(annexures):
                            matched = annexures[idx]
                    except ValueError:
                        matched = None
                if matched is not None:
                    ref.resolved = self._annexure_meta(matched)
                    resolved.append(ref.to_dict())
                else:
                    unresolved.append(ref.to_dict())
            elif ref.kind is ReferenceKind.SECTION:
                base = re.match(r"\d{1,3}", ref.target)
                ref.resolved = {"known": bool(base and base.group(0) in KNOWN_SECTIONS)}
                resolved.append(ref.to_dict())

        return {
            "references": [r.to_dict() for r in refs],
            "resolved": resolved,
            "unresolved": unresolved,
            "annexures": [self._annexure_meta(a) for a in annexures],
            "sections_known": sorted(KNOWN_SECTIONS),
        }

    # ------------------------------------------------------------------
    # Renumbering (pure, text + HTML)
    # ------------------------------------------------------------------
    def renumber_paragraphs(self, text: str) -> str:
        """Renumber line-start list markers (``1.`` / ``(1)``) sequentially.

        Only rewrites when the first marker starts a fresh list (1 / (1))
        so continuation numbering in plain text is never clobbered.
        """
        matches = list(_LIST_MARKER_RE.finditer(text))
        if not matches:
            return text
        first_marker = matches[0].group("paren") or matches[0].group("dot")
        if first_marker not in ("1", "01"):
            return text
        counter = {"n": 0}

        def _repl(match: re.Match[str]) -> str:
            counter["n"] += 1
            lead, sep = match.group("lead"), match.group("sep")
            if match.group("paren") is not None:
                return f"{lead}({counter['n']}){sep}"
            return f"{lead}{counter['n']}.{sep}"

        return _LIST_MARKER_RE.sub(_repl, text)

    def renumber_html_lists(self, html: str) -> str:
        """Recompute ``<ol start="N">`` continuation attributes.

        Only ``<ol class="justify">`` lists — the pattern used by the
        petition/permission templates (e.g. ``<ol class="justify" start="4">``
        continuing a 3-item justify list) — participate in the running
        sequence. Unrelated lists (witnesses, signatures, empty placeholders)
        are left untouched and do not disturb the counter. A document whose
        first list legitimately starts mid-sequence (a regenerated fragment)
        is left unchanged.
        """
        blocks = list(_OL_BLOCK_RE.finditer(html))
        if not blocks:
            return html
        # Guard: only renumber when the document opens a fresh sequence.
        first_start = _START_ATTR_RE.search(blocks[0].group(1))
        if first_start and first_start.group(2) not in ("1", "01"):
            return html

        prev_last = 0

        def _repl(match: re.Match[str]) -> str:
            nonlocal prev_last
            attrs, inner = match.group(1), match.group(2)
            if _NESTED_OL_RE.search(inner):
                return match.group(0)
            # Only justify lists are part of the document's numbered sequence.
            if 'class="justify"' not in attrs and "class='justify'" not in attrs:
                return match.group(0)
            li_count = len(re.findall(r"<li\b", inner, re.IGNORECASE))
            if li_count == 0:
                return match.group(0)
            start_match = _START_ATTR_RE.search(attrs)
            if start_match:
                new_start = prev_last + 1
                new_attrs = _START_ATTR_RE.sub(lambda m: f"{m.group(1)}{new_start}{m.group(3)}", attrs)
                prev_last = new_start + li_count - 1
                return f"<ol{new_attrs}>{inner}</ol>"
            prev_last = li_count
            return match.group(0)

        return _OL_BLOCK_RE.sub(_repl, html)

    # ------------------------------------------------------------------
    # Annexure letter renumbering (DB-backed)
    # ------------------------------------------------------------------
    def renumber_annexures(
        self, case_id: int | None = None, adjudication_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Reassign A/B/C... letters to annexures in upload order.

        Returns the list of ``{"annexure_id", "annexure_letter"}`` updates
        actually written. Callers should wrap in a request context.
        """
        from app.extensions import db

        annexures = self._load_annexures(case_id, adjudication_id)
        updates: list[dict[str, Any]] = []
        for index, annexure in enumerate(annexures[:26]):
            letter = _SECTION_LETTERS[index]
            if annexure.annexure_letter != letter:
                annexure.annexure_letter = letter
                updates.append({"annexure_id": annexure.id, "annexure_letter": letter})
        if updates:
            db.session.commit()
        return updates

    # ------------------------------------------------------------------
    # Enclosure list + HTML annotation (PDF-assembly entry point)
    # ------------------------------------------------------------------
    def build_enclosures_html(self, case_id: int | None = None, adjudication_id: int | None = None) -> str:
        """Generate the auto "List of Enclosures" ``<ol>`` from stored annexures.

        Returns an empty string when the case has no annexures.
        """
        annexures = self._load_annexures(case_id, adjudication_id)
        if not annexures:
            return ""
        items = []
        for annexure in annexures:
            page_note = ""
            if annexure.page_count:
                page_note = f", {annexure.page_count} page{'s' if annexure.page_count != 1 else ''}"
            items.append(
                f"<li>Copy of {_esc(annexure.caption)} — Annexure "
                f"{_esc(annexure.annexure_letter or '?')}{page_note}</li>"
            )
        return '<ol class="justify">\n' + "\n".join(items) + "\n</ol>"

    def annotate_html(
        self,
        html: str,
        case_id: int | None = None,
        adjudication_id: int | None = None,
    ) -> str:
        """Post-process rendered HTML before PDF compilation.

        1. Renumber ``<ol start=...>`` continuation attributes.
        2. Fill a ``<ol data-cross-reference="enclosures"></ol>`` placeholder
           with the auto-generated enclosures list when annexures exist.

        Never raises: on any unexpected failure the input is returned unchanged.
        """
        try:
            html = self.renumber_html_lists(html)
            if _ENCLOSURES_PLACEHOLDER_RE.search(html):
                enclosures = self.build_enclosures_html(case_id, adjudication_id)
                if enclosures:
                    html = _ENCLOSURES_PLACEHOLDER_RE.sub(lambda _m: enclosures, html, count=1)
            return html
        except Exception as exc:  # defensive — never break document generation
            logger.warning("Cross-reference annotation skipped: %s", exc)
            return html
