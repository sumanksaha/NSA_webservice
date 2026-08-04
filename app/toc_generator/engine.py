"""Dynamic Table of Contents generator (Phase 7).

Parses legal document HTML for headings (h1-h6), generates hierarchical
numbering (1, 1.1, 1.2, 2, ...), builds a nested <ol> TOC, and injects
it into <div data-toc></div> placeholders - following the same pattern
as the Phase 6 enclosures engine.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

logger = logging.getLogger(__name__)

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_TOC_PLACEHOLDER_RE = re.compile(
    r"<div\s[^>]*data-toc[^>]*>\s*</div>",
    re.IGNORECASE,
)

# Matches standalone annexure / appendix / enclosure / attachment markers
# such as "ANNEXURE A", "ANNEXURE - B", "APPENDIX I", "Annexure 1" or a bare
# "ANNEXURE". The ``(?![a-z])`` guard rejects the plural forms ("ANNEXURES",
# "APPENDICES") and the anchored ``$`` rejects descriptive titles like
# "Annexure Management" or "ANNEXURE A: LAB REPORT" so only true markers are
# flagged. Mirrored exactly in editor.js (buildToc) so the live panel and the
# server agree on what counts as an annexure.
_ANNEXURE_MARKER_RE = re.compile(
    r"^(annexure|appendix|enclosure|attachment)(?![a-z])"
    r"(?:\s*[-\u2013\u2014:.]?\s*(?:[a-z]{1,2}|[0-9]+|\[?[ivxlcdm]+\]?))?$",
    re.IGNORECASE,
)


@dataclass
class TocEntry:
    """A single heading extracted from a document."""

    level: int  # 1-6 (h1-h6)
    text: str
    heading_id: str  # unique HTML id for anchor links
    number: str = ""  # hierarchical number, e.g. "1.2.3"
    href: str = ""  # anchor href, e.g. "#toc-1"
    tag: str = ""  # heading tag name, e.g. "h2"
    is_annexure: bool = False  # True when text matches _ANNEXURE_MARKER_RE


class _HeadingExtractor(HTMLParser):
    """Extract h1-h6 headings from HTML, preserving order and nesting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._entries: list[TocEntry] = []
        self._counter = 0
        self._current_tag: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _HEADING_TAGS:
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == self._current_tag and tag in _HEADING_TAGS:
            text = "".join(self._current_text).strip()
            if text:
                # Only non-empty headings become entries, so they are the
                # only ones that consume an anchor id (ids stay sequential).
                self._counter += 1
                self._entries.append(
                    TocEntry(
                        level=int(tag[1]),
                        text=text,
                        heading_id=f"toc-{self._counter}",
                        tag=tag,
                    )
                )
            self._current_tag = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_tag is not None:
            self._current_text.append(data)

    @property
    def entries(self) -> list[TocEntry]:
        return self._entries


class TocGeneratorEngine:
    """Generate and inject a dynamic Table of Contents into legal documents.

    Public API:
    - extract_toc(html) -> list[TocEntry] (pure, no DB)
    - build_toc_html(entries) -> str of nested <ol>
    - annotate_html(html) -> HTML with TOC injected (PDF-assembly entry point)
    - generate_toc_data(html) -> JSON-safe dict for the UI
    """

    def extract_toc(self, html: str) -> list[TocEntry]:
        """Parse HTML and return an ordered list of TocEntry objects.

        Each entry gets a hierarchical number (e.g. "1.2.3") and
        an href ("#toc-1") for anchor navigation.
        """
        extractor = _HeadingExtractor()
        try:
            extractor.feed(html or "")
            extractor.close()
        except Exception:
            logger.warning("TOC extraction failed, returning empty list")
            return []

        entries = extractor.entries

        # Assign hierarchical numbers based on heading levels and flag
        # annexure/appendix markers so the TOC and PDF outline can
        # distinguish them from ordinary section headings.
        counters: list[int] = []
        for entry in entries:
            while len(counters) > entry.level:
                counters.pop()
            while len(counters) < entry.level:
                counters.append(0)
            counters[-1] += 1
            entry.number = ".".join(str(c) for c in counters)
            entry.href = f"#{entry.heading_id}"
            entry.is_annexure = bool(_ANNEXURE_MARKER_RE.match(entry.text))
        return entries

    # ------------------------------------------------------------------ #
    # TOC HTML generation (pure)                                          #
    # ------------------------------------------------------------------ #
    def build_toc_html(self, entries: list[TocEntry]) -> str:
        """Render a nested <ol> TOC from extracted entries.

        Each item is an <a> link with a numbered label. Handles arbitrary
        heading-level jumps (e.g. h1 -> h3 -> h1) by opening a nested
        sub-list under an <li> only when a deeper heading follows it, and
        closing it (together with its </li>) when the level unwinds.
        """
        if not entries:
            return '<ol class="toc-list"></ol>'

        lines: list[str] = ['<ol class="toc-list">']
        # Stack of open <li> elements: (level, has_sub_list). has_sub_list
        # records whether a nested <ol class="toc-sub"> was opened under it,
        # so the closing </ol> is only emitted when one actually exists.
        li_stack: list[tuple[int, bool]] = []

        for i, entry in enumerate(entries):
            level = entry.level

            if i > 0:
                if level > li_stack[-1][0]:
                    # Deeper heading: open a sub-list under the current <li>.
                    top_level, _ = li_stack.pop()
                    li_stack.append((top_level, True))
                    lines.append('<ol class="toc-sub">')
                else:
                    # Same level or shallower: close every open <li> whose
                    # depth is >= this entry's level before appending it.
                    while li_stack and li_stack[-1][0] >= level:
                        _, has_sub = li_stack.pop()
                        if has_sub:
                            lines.append("</ol>")
                        lines.append("</li>")

            number_span = f'<span class="toc-number">{entry.number}</span> ' if entry.number else ""
            badge = '<span class="toc-annexure-badge">Annexure</span> ' if entry.is_annexure else ""
            item_class = f"toc-item level-{level}"
            if entry.is_annexure:
                item_class += " toc-annexure"
            lines.append(
                f'<li class="{item_class}">' f'<a href="{entry.href}">{number_span}{badge}{html.escape(entry.text)}</a>'
            )
            li_stack.append((level, False))

        # Close any remaining open <li> elements, then the root list.
        while li_stack:
            _, has_sub = li_stack.pop()
            if has_sub:
                lines.append("</ol>")
            lines.append("</li>")
        lines.append("</ol>")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Heading annotation (inject id attributes for anchor links)          #
    # ------------------------------------------------------------------ #
    def annotate_headings(self, html: str, entries: list[TocEntry]) -> str:
        """Add id attributes to heading tags so TOC anchors resolve.

        Processes all headings in document order with a single regex
        pass, matching each non-empty heading to the next entry. This
        avoids the count=1 + reversed-order problem where the first
        heading of a given level always wins.
        """
        if not entries:
            return html

        pattern = re.compile(
            r"<(h[1-6])([^>]*)>(.*?)</\1>",
            re.IGNORECASE | re.DOTALL,
        )

        entry_idx = 0

        def _repl(m: re.Match[str]) -> str:
            nonlocal entry_idx
            attrs = m.group(2)
            tag = m.group(1).lower()
            inner = m.group(3)
            stripped = inner.strip()
            if not stripped:
                return m.group(0)  # skip empty headings
            if "id=" in attrs:
                return m.group(0)  # already annotated
            if entry_idx < len(entries):
                entry = entries[entry_idx]
                entry_idx += 1
                return f'<{tag}{attrs} id="{entry.heading_id}">{inner}</{tag}>'
            return m.group(0)

        return pattern.sub(_repl, html)

    # ------------------------------------------------------------------ #
    # Full annotation (PDF-assembly entry point)                          #
    # ------------------------------------------------------------------ #
    def annotate_html(self, html: str) -> str:
        """Post-process rendered HTML: inject TOC into <div data-toc>.

        1. Extract headings and number them.
        2. Add id attributes to headings for anchor links.
        3. Replace <div data-toc></div> with the generated TOC HTML.

        Never raises: returns the input unchanged on failure.
        """
        try:
            entries = self.extract_toc(html)
            if not entries:
                return html
            html = self.annotate_headings(html, entries)
            toc_html = self.build_toc_html(entries)
            if _TOC_PLACEHOLDER_RE.search(html):

                def _nav_repl(_m: re.Match[str]) -> str:
                    return (
                        '<nav class="toc-nav" role="navigation" aria-label="Table of Contents">' f"{toc_html}" "</nav>"
                    )

                html = _TOC_PLACEHOLDER_RE.sub(_nav_repl, html, count=1)
            return html
        except Exception as exc:
            logger.warning("TOC annotation skipped: %s", exc)
            return html

    # ------------------------------------------------------------------ #
    # JSON-safe report for the UI                                         #
    # ------------------------------------------------------------------ #
    def generate_toc_data(self, html: str) -> dict[str, Any]:
        """Return JSON-safe TOC data for the report UI.

        Keys: entries, total_headings, total_annexures, max_depth,
        has_toc_placeholder.
        """
        entries = self.extract_toc(html)
        has_placeholder = bool(_TOC_PLACEHOLDER_RE.search(html or ""))
        max_depth = max((e.level for e in entries), default=0)
        return {
            "entries": [
                {
                    "level": e.level,
                    "text": e.text,
                    "heading_id": e.heading_id,
                    "number": e.number,
                    "href": e.href,
                    "tag": e.tag,
                    "is_annexure": e.is_annexure,
                }
                for e in entries
            ],
            "total_headings": len(entries),
            "total_annexures": sum(1 for e in entries if e.is_annexure),
            "max_depth": max_depth,
            "has_toc_placeholder": has_placeholder,
        }
