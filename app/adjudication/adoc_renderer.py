"""Render Jinja2-templated .adoc files → .docx via pandoc.

Single source of truth: .adoc templates (same as preview HTML pipeline).
Uses pandoc when available; falls back to python-docx for local development.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from docx import Document
from docx.shared import Inches

if TYPE_CHECKING:
    from flask import Flask

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "adjudication"


def render_adoc_to_docx(template_name: str, context: dict, app: "Flask | None" = None) -> bytes:
    """Render an .adoc template with Jinja2, then convert to DOCX via pandoc.

    Args:
        template_name: e.g. "template_nonsample_petition.adoc"
        context: dict passed to Jinja2 render (same as preview uses)
        app: Flask app instance (for render_template_string). If None, uses current_app.

    Returns:
        bytes: DOCX file content

    Raises:
        RuntimeError: if pandoc not available or conversion fails
    """
    adoc_path = TEMPLATE_DIR / template_name
    if not adoc_path.exists():
        raise FileNotFoundError(f"Adoc template not found: {adoc_path}")

    adoc_source = adoc_path.read_text(encoding="utf-8")

    # Jinja2-render the same way preview does
    from flask import render_template_string

    if app:
        with app.app_context():
            rendered_adoc = render_template_string(adoc_source, **context)
    else:
        rendered_adoc = render_template_string(adoc_source, **context)

    # Pandoc: HTML → docx (the .adoc files are HTML)
    try:
        result = subprocess.run(
            ["pandoc", "-f", "html", "-t", "docx", "-o", "-"],
            input=rendered_adoc,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[:500])
        return result.stdout.encode("utf-8")
    except (RuntimeError, FileNotFoundError):
        # Fallback: render via python-docx with proper context substitution
        return _fallback_docx_from_adoc(adoc_source, context, app)


def is_pandoc_available() -> bool:
    """Check if pandoc binary is on PATH."""
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _fallback_docx_from_adoc(adoc_source: str, context: dict, app: "Flask | None" = None) -> bytes:
    """Fallback DOCX renderer when pandoc is unavailable.

    Renders the .adoc template with Jinja2 (same as preview), then builds a
    simple python-docx document from the rendered text.
    """
    # Re-render Jinja2 from source (same as main path)
    from flask import render_template_string

    if app:
        with app.app_context():
            rendered = render_template_string(adoc_source, **context)
    else:
        rendered = render_template_string(adoc_source, **context)

    # Strip AsciiDoc formatting markers to get plain text
    text = _strip_asciidoc(rendered)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip AsciiDoc directives and block markers
        if stripped.startswith(">") or stripped.startswith(":") or stripped.startswith("["):
            continue
        doc.add_paragraph(stripped)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _strip_asciidoc(text: str) -> str:
    """Remove AsciiDoc formatting markers, keeping readable text."""
    # Remove attribute lists like {: .bold} or {attribute}
    text = re.sub(r"\{:[^{}]*\}", "", text)
    text = re.sub(r"\{[a-zA-Z_][^{}]*\}", "", text)
    # Bold/italic inline
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Remove AsciiDoc block markers
    text = re.sub(r"^=+\s*$", "", text, flags=re.MULTILINE)
    # Remove image macros
    text = re.sub(r"image::?[^\s\"\[\]]+[^\n]*", "", text)
    # Remove table separators but keep content
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|==") or stripped.startswith("|---"):
            continue
        if re.match(r"^\|.*\|$", stripped):
            # Pipe-delimited: strip pipes and whitespace
            content = stripped.strip("|").strip()
            lines.append(content if content else "—")
        else:
            lines.append(line)
    return "\n".join(lines)
