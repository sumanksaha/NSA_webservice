"""Shared .adoc → .docx renderer (ADR-001 pipeline).

Renders a Jinja2-templated AsciiDoc template and converts it to DOCX:

1. Jinja2 renders the ``.adoc`` source with the same context used by the
   HTML/PDF pipeline (ADR-001: all document logic lives in Jinja2).
2. Pandoc (``-f asciidoc -t docx``) produces the Word document when the
   binary is available.
3. Otherwise a python-docx fallback builds a readable document from the
   rendered AsciiDoc (roles/markers stripped, entities unescaped).

Blueprints bind their own template directory via a thin wrapper, e.g.
``app/case_file_generator/adoc_renderer.py``.  Must be called inside a
Flask app/request context (Jinja2 rendering).
"""

from __future__ import annotations

import html
import io
import re
import subprocess
from pathlib import Path

from docx import Document
from docx.shared import Inches

# [.role]#text# / [#id]#text# — AsciiDoc role/anchor quoted text
_ROLE_TEXT_RE = re.compile(r"\[[.#][^\]]*\]#(.*?)#")
# Standalone block modifiers like [.justify], [.container], [#section-51]
_BLOCK_ATTR_RE = re.compile(r"^\[[.#][^\]]*\]$")
# Section-style headings written as "-- TITLE --"
_DASHED_TITLE_RE = re.compile(r"^--\s+(.+?)\s+--$")


def render_adoc_to_docx(template_dir: Path | str, template_name: str, context: dict) -> bytes:
    """Render ``<template_dir>/<template_name>`` with ``context`` and return DOCX bytes.

    Raises:
        FileNotFoundError: if the template does not exist.
    """
    adoc_path = Path(template_dir) / template_name
    if not adoc_path.exists():
        raise FileNotFoundError(f"Adoc template not found: {adoc_path}")

    source = adoc_path.read_text(encoding="utf-8")

    from flask import render_template_string

    rendered = render_template_string(source, **context)

    docx_bytes = _pandoc_asciidoc_to_docx(rendered)
    if docx_bytes:
        return docx_bytes
    return _fallback_docx_from_adoc(rendered)


def _pandoc_asciidoc_to_docx(rendered_adoc: str) -> bytes | None:
    """Convert rendered AsciiDoc to DOCX via pandoc.

    Returns None when pandoc is unavailable, lacks the asciidoc reader, or
    fails — the caller falls back to the python-docx renderer.

    Binary mode throughout: ``pandoc -t docx -o -`` streams the DOCX (a ZIP
    archive) to stdout, which must never be decoded as text.
    """
    try:
        result = subprocess.run(
            ["pandoc", "-f", "asciidoc", "-t", "docx", "-o", "-"],
            input=rendered_adoc.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _fallback_docx_from_adoc(rendered: str) -> bytes:
    """python-docx fallback: readable plain-text document from rendered AsciiDoc."""
    # Passthrough blocks (style-only in our templates) carry no document text.
    rendered = re.sub(r"\+\+\+\+.*?\+\+\+\+", "", rendered, flags=re.DOTALL)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)

    list_counter = 0
    in_table = False
    for raw_line in rendered.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            list_counter = 0
            continue
        if stripped.startswith("|==="):
            in_table = not in_table
            list_counter = 0
            continue
        if in_table:
            # AsciiDoc table rows are pipe-delimited; render each line's cells.
            cells = [_strip_inline_markup(part) for part in stripped.split("|")]
            cells = [cell for cell in cells if cell]
            if cells:
                doc.add_paragraph("    ".join(cells))
            continue
        if stripped == "<<<":
            doc.add_page_break()
            list_counter = 0
            continue
        if stripped in ("++++", "+", "'''", "--"):
            list_counter = 0
            continue
        if _BLOCK_ATTR_RE.match(stripped):
            continue

        dashed = _DASHED_TITLE_RE.match(stripped)
        if dashed:
            doc.add_heading(_strip_inline_markup(dashed.group(1)), level=2)
            list_counter = 0
            continue

        text = _strip_inline_markup(stripped)
        if not text:
            continue
        if stripped.startswith(". "):
            list_counter += 1
            doc.add_paragraph(f"{list_counter}. {text}")
        else:
            list_counter = 0
            doc.add_paragraph(text)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _strip_inline_markup(text: str) -> str:
    """Strip AsciiDoc inline formatting and residual HTML, keeping readable text."""
    # Role/anchor quoted text first: [.role]#content# → content
    text = _ROLE_TEXT_RE.sub(r"\1", text)
    # Bold / italic markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)
    # Unconstrained strong: #text# (incl. <strong> remnants from the migration)
    text = re.sub(r"#([^#]+)#", r"\1", text)
    # Residual inline HTML tags (e.g. <strong> kept inside #…#)
    text = re.sub(r"<[^>]+>", " ", text)
    # Hard-break markers at end of line
    text = re.sub(r"\s+\+\s*$", "", text)
    # HTML entities (literal &amp; in source and autoescaped variables)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
