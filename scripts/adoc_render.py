#!/usr/bin/env python3
"""
Convert .adoc templates to HTML (for WeasyPrint PDF) and DOCX (for Word).

Pipeline:
  1. Map .adoc -> archived HTML (has valid Jinja2 syntax)
  2. Render archived HTML through Jinja2 with context
  3. HTML -> PDF (WeasyPrint) OR HTML -> DOCX (Pandoc)

The .adoc files are source records with AsciiDoc markup preserved.
Archived HTML files have the actual Jinja2 templates.

Usage:
    python scripts/adoc_render.py <input.adoc> [output_dir]
    python scripts/adoc_render.py <input.adoc> <output_dir> [--format html|pdf|docx|all]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Map each .adoc to its archived HTML counterpart
ADOC_TO_HTML_ARCHIVE = {
    "app/adjudication/templates/adjudication/Legal_NonsampleAdjudication_Template.adoc": "app/adjudication/templates/adjudication/archive/Legal_NonsampleAdjudication_Template.html",
    "app/adjudication/templates/adjudication/template_nonsample_petition.adoc": "app/adjudication/templates/adjudication/archive/template_nonsample_petition.html",
    "app/case_file_generator/templates/case_file_generator/petition.adoc": "app/case_file_generator/templates/case_file_generator/archive/petition.html",
    "app/case_file_generator/templates/case_file_generator/permission_letter.adoc": "app/case_file_generator/templates/case_file_generator/archive/permission_letter.html",
}


def find_archive_html(adoc_path: str) -> str:
    """Map .adoc path to its archived HTML counterpart."""
    rel = os.path.relpath(adoc_path)
    if rel in ADOC_TO_HTML_ARCHIVE:
        return ADOC_TO_HTML_ARCHIVE[rel]
    # Fallback: try same name in archive/ sibling
    p = Path(adoc_path)
    archive = p.parent / "archive" / p.with_suffix(".html").name
    if archive.exists():
        return str(archive)
    raise FileNotFoundError(f"No archived HTML found for {adoc_path}")


def render_html_with_jinja(html_path: str, context: dict | None = None) -> str:
    """Render archived HTML file through Jinja2."""
    try:
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"ERROR: Failed to read {html_path}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        from jinja2 import BaseLoader, Environment

        env = Environment(loader=BaseLoader(), autoescape=True)
        template = env.from_string(content)
        rendered = template.render(**(context or {}))
        return rendered
    except Exception as e:
        print(f"ERROR: Jinja2 rendering failed: {e}", file=sys.stderr)
        sys.exit(1)


def write_html(rendered: str, output_dir: str, basename: str) -> str:
    """Write rendered HTML to output directory."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{basename}.html")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(rendered)
        return out_path
    except Exception as e:
        print(f"ERROR: Failed to write HTML output: {e}", file=sys.stderr)
        sys.exit(1)


def html_to_pdf(html_path: str, output_dir: str, basename: str) -> str:
    """Convert rendered HTML to PDF using WeasyPrint."""
    try:
        from weasyprint import HTML
    except ImportError:
        print("ERROR: WeasyPrint not installed. pip install weasyprint", file=sys.stderr)
        sys.exit(1)

    try:
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{basename}.pdf")
        with open(html_path, encoding="utf-8") as f:
            html_string = f.read()
        HTML(string=html_string).write_pdf(out_path)
        return out_path
    except Exception as e:
        print(f"ERROR: WeasyPrint failed: {e}", file=sys.stderr)
        sys.exit(1)


def html_to_docx(html_path: str, output_dir: str, basename: str) -> str:
    """Convert rendered HTML to DOCX using Pandoc (HTML -> DOCX)."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{basename}.docx")
        cmd = ["pandoc", "-f", "html", "-t", "docx", "-o", out_path, html_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR: pandoc failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        return out_path
    except Exception as e:
        print(f"ERROR: Failed to run pandoc: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Convert .adoc templates to HTML/PDF/DOCX via archived HTML + Jinja2")
    parser.add_argument("input", help="Input .adoc file (source record)")
    parser.add_argument("output_dir", help="Output directory")
    parser.add_argument(
        "--format",
        choices=["html", "pdf", "docx", "all"],
        default="all",
        help="Output format",
    )
    parser.add_argument("--context", help="JSON context for Jinja2 rendering", default="{}")
    args = parser.parse_args()

    try:
        context = json.loads(args.context)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON context: {e}", file=sys.stderr)
        sys.exit(1)

    adoc_path = args.input
    basename = Path(adoc_path).stem

    # 1. Find archived HTML
    archive_html = find_archive_html(adoc_path)
    print(f"Using archived HTML: {archive_html}")

    # 2. Render HTML with Jinja2
    rendered_html = render_html_with_jinja(archive_html, context)
    html_out = write_html(rendered_html, args.output_dir, basename)
    print(f"HTML rendered: {html_out}")

    # 3. Convert to requested formats
    if args.format in ("pdf", "all"):
        pdf_out = html_to_pdf(html_out, args.output_dir, basename)
        print(f"PDF rendered: {pdf_out}")

    if args.format in ("docx", "all"):
        docx_out = html_to_docx(html_out, args.output_dir, basename)
        print(f"DOCX rendered: {docx_out}")

    print("Done.")


if __name__ == "__main__":
    main()
