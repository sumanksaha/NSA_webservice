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
import shutil
import subprocess
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

# [.role]#text# / [#id]#text# — AsciiDoc role/anchor quoted text
_ROLE_TEXT_RE = re.compile(r"\[[.#][^\]]*\]#(.*?)#")
# Standalone block modifiers like [.justify], [.container], [#section-51]
_BLOCK_ATTR_RE = re.compile(r"^\[[.#][^\]]*\]$")
# Section-style headings written as "-- TITLE --"
_DASHED_TITLE_RE = re.compile(r"^--\s+(.+?)\s+--$")

# Upper bound for a single pandoc conversion. Real templates render in well
# under a second, so anything slower is a hung/misbehaving converter.
PANDOC_TIMEOUT_SECONDS = 30


def _pandoc_cmd(*args: str) -> list[str]:
    """Resolve the pandoc binary to a full path (S607: no PATH lookups).

    Raises FileNotFoundError when pandoc is not installed — the caller
    treats that as "use the python-docx fallback".
    """
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise FileNotFoundError("pandoc binary not found on PATH")
    return [pandoc, *args]


def _render_source(template_dir: Path | str, template_name: str, context: dict) -> str:
    """Load the .adoc template and render it with *context* via Jinja2."""
    adoc_path = Path(template_dir) / template_name
    if not adoc_path.exists():
        raise FileNotFoundError(f"Adoc template not found: {adoc_path}")
    source = adoc_path.read_text(encoding="utf-8")
    from flask import render_template_string

    return render_template_string(source, **context)


def render_adoc_to_docx(
    template_dir: Path | str, template_name: str, context: dict, output_format: str = "docx"
) -> bytes | str:
    """Render ``<template_dir>/<template_name>`` with ``context`` and return DOCX bytes.

    Args:
        template_dir: directory containing the .adoc template
        template_name: name of the .adoc file
        context: dict passed to Jinja2 render
        output_format: "docx" or "adoc"

    Returns:
        bytes: DOCX content when output_format="docx"
        str: rendered AsciiDoc when output_format="adoc"

    Raises:
        FileNotFoundError: if the template does not exist.
        ValueError: if output_format is not "docx" or "adoc".

    """
    rendered = _render_source(template_dir, template_name, context)

    if output_format == "adoc":
        return rendered
    if output_format == "docx":
        docx_bytes = _pandoc_asciidoc_to_docx(rendered)
        if docx_bytes:
            return docx_bytes
        return _fallback_docx_from_adoc(rendered)
    raise ValueError(f"Unknown output_format: {output_format}. Use 'docx' or 'adoc'")


def _pandoc_asciidoc_to_docx(rendered_adoc: str) -> bytes | None:
    """Convert rendered AsciiDoc to DOCX via pandoc.

    Returns None when pandoc is unavailable, lacks the asciidoc reader, times
    out, or fails — the caller falls back to the python-docx renderer.

    The timeout matters: pandoc's asciidoc reader hangs (and grows without
    bound) on malformed input such as an unterminated ``////`` comment block,
    so a template typo must degrade to the fallback instead of wedging the
    request — or the CI runner — forever.

    Binary mode throughout: ``pandoc -t docx -o -`` streams the DOCX (a ZIP
    archive) to stdout, which must never be decoded as text.
    """
    try:
        # Fixed argv, no shell: the only variable element is the resolved binary path.
        result = subprocess.run(  # noqa: S603
            _pandoc_cmd("-f", "asciidoc", "-t", "docx", "-o", "-"),
            input=rendered_adoc.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=PANDOC_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _fallback_docx_from_adoc(rendered: str) -> bytes:
    """python-docx fallback: readable, styled Word document from rendered AsciiDoc.

    Interprets the AsciiDoc block/inline role attributes our templates already
    declare (``[.justify]``, ``[.bold]``, ``[.center]``, ``[.header]``,
    ``[.section-title]``, ``[.legal-notice-box]``, ``[.signature]``,
    ``[.entry]``, ``[.entry-title]``, ``[.footer-table]``) as real Word
    formatting instead of stripping them, so the generated DOCX keeps the
    intended emphasis, alignment, boxes, and tables.
    """
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
    table_buf: list[str] = []
    pending_role: str | None = None  # role carried from a [.role] block attribute

    def _set_alignment(paragraph, role: str) -> None:
        if role in ("center", "header"):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif role == "justify":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        elif role in ("right", "signature"):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    def _apply_inline_runs(paragraph, text: str, bold: bool = False) -> None:
        """Add text to *paragraph* as runs, honouring **[bold]** inline markup.

        ``text`` is raw (not yet stripped): ``**...**`` segments become bold
        runs; the remaining inline markup (``##``, ``__``, ``_``, HTML, entities)
        is stripped from non-bold segments.
        """
        # Split on **bold** segments so we can bold sub-runs.
        parts = re.split(r"(\*\*[^*]+\*\*)", text)
        for part in parts:
            if not part:
                continue
            m = re.fullmatch(r"\*\*([^*]+)\*\*", part)
            inner = m.group(1) if m else part
            inner = _strip_inline_markup(inner)
            if not inner:
                continue
            run = paragraph.add_run(inner)
            run.bold = bold or bool(m)

    def _add_paragraph(raw_text: str, role: str | None = None, bold: bool = False) -> None:
        nonlocal list_counter
        text = raw_text
        if text.startswith(". "):
            list_counter += 1
            text = f"{list_counter}. {text[2:]}"
        else:
            list_counter = 0
        paragraph = doc.add_paragraph()
        if role:
            _set_alignment(paragraph, role)
        if role == "section-title":
            text = text.upper()
            run = paragraph.add_run(text)
            run.bold = True
            run.underline = True
        else:
            _apply_inline_runs(paragraph, text, bold=bold)
        if role in ("bold", "header"):
            for run in paragraph.runs:
                run.bold = True

    def _add_legal_notice_box(text: str) -> None:
        """Render *text* as a bordered, shaded single-cell box (legal notice)."""
        tbl = doc.add_table(rows=1, cols=1)
        tbl.style = "Table Grid"
        cell = tbl.cell(0, 0)
        # Shade the cell background light grey.
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "F2F2F2")
        tc_pr.append(shd)
        # Add a border around the cell.
        borders = OxmlElement("w:tcBorders")
        for edge in ("top", "left", "bottom", "right"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "8")
            el.set(qn("w:space"), "4")
            el.set(qn("w:color"), "2C3E50")
            borders.append(el)
        tc_pr.append(borders)
        p = cell.paragraphs[0]
        _apply_inline_runs(p, text)
        for run in p.runs:
            run.bold = True
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def _flush_table() -> None:
        """Emit buffered AsciiDoc table lines as a real Word grid table."""
        if not table_buf:
            return
        logical_rows: list[str] = []
        acc = ""
        for line in table_buf:
            if line.endswith("+"):
                acc += line[:-1] + " "
            else:
                acc += line
                logical_rows.append(acc)
                acc = ""
        if acc:
            logical_rows.append(acc)
        rows: list[list[str]] = []
        max_cols = 0
        for row in logical_rows:
            parts = row.split("|")
            if parts and not parts[0].strip():
                parts = parts[1:]
            if parts and not parts[-1].strip() and row.rstrip().endswith("|"):
                parts = parts[:-1]
            cells = [_strip_inline_markup(part) for part in parts]
            if not cells:
                continue
            rows.append(cells)
            max_cols = max(max_cols, len(cells))
        if not rows or max_cols == 0:
            return
        for r in rows:
            while len(r) < max_cols:
                r.append("")
        tbl = doc.add_table(rows=len(rows), cols=max_cols)
        tbl.style = "Table Grid"
        for ri, r in enumerate(rows):
            for ci, cell_text in enumerate(r):
                tbl.cell(ri, ci).text = cell_text

    for raw_line in rendered.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            list_counter = 0
            continue
        if stripped.startswith("|==="):
            if in_table:
                _flush_table()
                table_buf.clear()
            in_table = not in_table
            list_counter = 0
            continue
        if in_table:
            table_buf.append(stripped)
            continue
        if stripped == "<<<":
            doc.add_page_break()
            list_counter = 0
            continue
        if stripped in ("++++", "+", "'''", "--"):
            list_counter = 0
            continue

        # Standalone block attribute: carry its role to the next paragraph.
        m = _BLOCK_ATTR_RE.match(stripped)
        if m:
            pending_role = stripped.strip("[]").lstrip(".")
            list_counter = 0
            continue

        dashed = _DASHED_TITLE_RE.match(stripped)
        if dashed:
            doc.add_heading(_strip_inline_markup(dashed.group(1)), level=2)
            list_counter = 0
            pending_role = None
            continue

        role = pending_role
        pending_role = None

        # Legal notice box: a bordered/shaded single-cell box.
        if role == "legal-notice-box":
            _add_legal_notice_box(stripped)
            continue

        # Footer table: borderless table (handled below as a normal table).
        if role == "footer-table" and stripped.startswith("|==="):
            table_buf.append(stripped)
            continue

        raw_text = stripped
        if not raw_text:
            continue

        bold = role == "bold"
        if role == "section-title":
            _add_paragraph(raw_text, role="section-title", bold=True)
        elif role == "entry-title":
            p = doc.add_paragraph()
            _set_alignment(p, None)
            run = p.add_run(_strip_inline_markup(raw_text))
            run.bold = True
            list_counter = 0
        elif role == "entry":
            p = doc.add_paragraph()
            _set_alignment(p, None)
            _apply_inline_runs(p, raw_text)
            list_counter = 0
        else:
            _add_paragraph(raw_text, role=role, bold=bold)

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
