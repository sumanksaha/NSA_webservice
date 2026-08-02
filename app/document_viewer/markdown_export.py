"""Quill Delta → Markdown conversion (Phase 2: Markdown export).

Converts a Quill 2.x Delta document (as produced by ``quill.getContents()``)
into GitHub-flavoured Markdown.  No third-party dependency is required — the
conversion is implemented directly on the Delta ops model.

Supported formats:
  - headings (H1-H6), blockquotes, code blocks, alignment (as HTML divs)
  - ordered/bullet lists with indentation
  - bold / italic / underline / strike / inline code
  - links, images, videos, formulas (as embeds)

Unsupported embeds degrade to their raw JSON representation.  Quill table
cells are rendered as plain text lines (no pipe-table conversion yet).
"""

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------


def _render_inline(text: str, attrs: dict[str, Any]) -> str:
    """Wrap a run of text with markdown for its inline attributes."""
    if not text:
        return ""

    if attrs.get("code"):
        text = f"`{text}`"
    if attrs.get("link"):
        text = f"[{text}]({attrs['link']})"
    if attrs.get("bold"):
        text = f"**{text}**"
    if attrs.get("italic"):
        text = f"*{text}*"
    if attrs.get("strike"):
        text = f"~~{text}~~"
    if attrs.get("underline"):
        text = f"<u>{text}</u>"
    return text


def _render_embed(value: dict[str, Any]) -> str:
    """Render an embed (image/video/formula) as markdown."""
    if "image" in value:
        return f"![image]({value['image']})"
    if "video" in value:
        return f"[video]({value['video']})"
    if "formula" in value:
        return f"${value['formula']}$"
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Block rendering
# ---------------------------------------------------------------------------


def _render_block(attrs: dict[str, Any], inline: str) -> str:
    """Render a completed line using its block-level attributes."""
    header = attrs.get("header")
    if header:
        level = max(1, min(int(header), 6))
        return "#" * level + " " + inline if inline else "#" * level

    if attrs.get("code-block"):
        return "```\n" + inline + "\n```"

    if attrs.get("blockquote"):
        return "> " + inline if inline else ">"

    list_type = attrs.get("list")
    if list_type == "ordered":
        prefix = "1. "
    elif list_type == "bullet":
        prefix = "- "
    else:
        prefix = None

    if prefix is not None:
        indent = int(attrs.get("indent") or 0)
        return "    " * indent + prefix + inline

    align = attrs.get("align")
    if align in ("center", "right", "justify"):
        return f'<div align="{align}">{inline}</div>'

    return inline


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def delta_to_markdown(delta: dict[str, Any] | None) -> str:
    """Convert a Quill Delta dict to Markdown text.

    Args:
        delta: The Delta document, e.g. ``{"ops": [...]}``.

    Returns:
        Markdown string. Empty string for ``None``/empty input.

    """
    if not delta or not isinstance(delta, dict):
        return ""

    ops = delta.get("ops") or []
    blocks: list[tuple[dict[str, Any], str]] = []
    current_inline: list[str] = []

    for op in ops:
        if not isinstance(op, dict):
            continue
        insert = op.get("insert")
        attrs = op.get("attributes") or {}

        if isinstance(insert, dict):
            current_inline.append(_render_embed(insert))
            continue
        if not isinstance(insert, str):
            continue

        # A string op may contain multiple lines.  The op that terminates a
        # line with ``\\n`` carries that line's block attributes.
        segments = insert.split("\n")
        for idx, segment in enumerate(segments):
            if idx > 0:
                blocks.append((dict(attrs), "".join(current_inline)))
                current_inline = []
            if segment:
                current_inline.append(_render_inline(segment, attrs))

    if current_inline:
        blocks.append(({}, "".join(current_inline)))

    # Drop a single trailing empty block (Quill always appends "\\n").
    if len(blocks) == 1 and not blocks[0][1] and not blocks[0][0]:
        return ""

    lines = [_render_block(attrs, inline) for attrs, inline in blocks]
    return "\n".join(lines)


def html_to_markdown(html: str) -> str:
    """Minimal HTML → Markdown fallback for when only HTML is available.

    Handles the subset of tags the Quill editor emits for the petition /
    permission documents.  Not a full HTML parser — the Delta path is
    preferred and used by the frontend.
    """
    if not html:
        return ""

    def _heading(match: re.Match[str], level: int = 1) -> str:
        """Render a matched heading tag as markdown."""
        text = _strip_tags(match.group(1)).strip()
        return "#" * level + (" " + text if text else "")

    def _quote(match: re.Match[str]) -> str:
        """Render a matched blockquote as markdown."""
        return "> " + _strip_tags(match.group(1)).strip()

    def _link(match: re.Match[str]) -> str:
        """Render a matched anchor as markdown."""
        return f"[{_strip_tags(match.group(2))}]({match.group(1)})"

    def _image(match: re.Match[str]) -> str:
        """Render a matched img tag as markdown."""
        return f"![image]({match.group(1)})"

    # Headings
    from functools import partial

    for level in range(6, 0, -1):
        html = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            partial(_heading, level=level),
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # Blockquotes
    html = re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>",
        _quote,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Bold / italic / underline / strike / code
    html = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<(u)[^>]*>(.*?)</\1>", r"<u>\2</u>", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<(s|strike|del)[^>]*>(.*?)</\1>", r"~~\2~~", html, flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.IGNORECASE | re.DOTALL)

    # Links
    html = re.sub(
        r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        _link,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Images
    html = re.sub(r'<img[^>]*src="([^"]*)"[^>]*>', _image, html, flags=re.IGNORECASE)

    # Line breaks / paragraphs
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</p>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(?:div|li|h[1-6]|tr)>", "\n", html, flags=re.IGNORECASE)

    return _strip_tags(html).strip()


def _strip_tags(text: str) -> str:
    """Remove remaining HTML tags from a snippet."""
    return re.sub(r"<[^>]+>", "", text)
