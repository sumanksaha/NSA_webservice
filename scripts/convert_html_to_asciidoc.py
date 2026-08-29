#!/usr/bin/env python3
"""
Improved script to convert HTML legal document templates to AsciiDoc format.

Better HTML parsing and AsciiDoc generation for maintainability while
preserving HTML templates as reference.

Author: NSA Webservice Team
Date: 2026-08-26
"""

import re
import os
from pathlib import Path
from typing import List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_html_for_asciidoc(html_content: str) -> tuple[str, List[str], List[str], List[str]]:
    """Parse HTML and extract AsciiDoc content, tables, lists, and paragraphs."""
    asciidoc_sections = []
    tables = []
    lists = []
    paragraphs = []

    # Remove scripts and styles
    cleaned_html = re.sub(r'<script.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    cleaned_html = re.sub(r'<style.*?</style>', '', cleaned_html, flags=re.DOTALL | re.IGNORECASE)

    # Extract HTML comments as metadata
    html_comments = re.findall(r'<!--(.*?)-->', html_content, re.DOTALL)

    # Process headings
    h1_pattern = r'<h1[^>]*>(.*?)</h1>'
    for match in re.finditer(h1_pattern, html_content, re.DOTALL | re.IGNORECASE):
        text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if text:
            asciidoc_sections.append(f"# {text}")

    h2_pattern = r'<h2[^>]*>(.*?)</h2>'
    for match in re.finditer(h2_pattern, html_content, re.DOTALL | re.IGNORECASE):
        text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if text:
            asciidoc_sections.append(f"## {text}")

    # Process paragraphs with better content extraction
    p_pattern = r'<p[^>]*>(.*?)</p>'
    for match in re.finditer(p_pattern, html_content, re.DOTALL | re.IGNORECASE):
        text = re.sub(r'<[^>]+>', ' ', match.group(1)).strip()
        # Clean up extra whitespace and preserve line breaks
        text = re.sub(r'\s+', ' ', text)
        if text:
            paragraphs.append(text)

    # Process tables more robustly
    table_pattern = r'<table[^>]*>(.*?)</table>'
    for table_match in re.finditer(table_pattern, html_content, re.DOTALL | re.IGNORECASE):
        table_html = table_match.group(1)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
        if rows:
            table_lines = []
            headers = []

            # First row as headers
            header_cells = re.findall(r'<th[^>]*>(.*?)</th>', rows[0], re.DOTALL | re.IGNORECASE)
            if header_cells:
                headers = [re.sub(r'<[^>]+>', '', cell).strip() for cell in header_cells]

            # Remaining rows as data
            data_rows = rows[1:] if len(rows) > 1 else []
            table_lines.append('|===')

            if headers:
                table_lines.append('| ' + ' | '.join(headers) + ' |')
                table_lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')

            for row in data_rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                if cells:
                    cleaned_cells = [re.sub(r'<[^>]+>', '', cell).strip() for cell in cells]
                    table_lines.append('| ' + ' | '.join(cleaned_cells) + ' |')

            table_lines.append('|===')
            tables.append('\n'.join(table_lines))

    # Process lists
    ul_pattern = r'<ul[^>]*>(.*?)</ul>'
    for ul_match in re.finditer(ul_pattern, html_content, re.DOTALL | re.IGNORECASE):
        ul_html = ul_match.group(1)
        items = re.findall(r'<li[^>]*>(.*?)</li>', ul_html, re.DOTALL | re.IGNORECASE)
        if items:
            list_lines = []
            for item in items:
                text = re.sub(r'<[^>]+>', ' ', item).strip()
                text = re.sub(r'\s+', ' ', text)
                list_lines.append(f'* {text}')
            lists.append('\n'.join(list_lines))

    # Process bold and italic text in remaining content
    # Convert remaining HTML tags to AsciiDoc formatting
    remaining_content = re.sub(r'<p[^>]*>', '', html_content)
    remaining_content = re.sub(r'</p>', '\n', remaining_content)

    # Convert bold to **text**
    remaining_content = re.sub(r'<b>(.*?)</b>', r'**\1**', remaining_content, flags=re.DOTALL | re.IGNORECASE)
    remaining_content = re.sub(r'<strong>(.*?)</strong>', r'**\1**', remaining_content, flags=re.DOTALL | re.IGNORECASE)

    # Convert italic to *text*
    remaining_content = re.sub(r'<i>(.*?)</i>', r'*\1*', remaining_content, flags=re.DOTALL | re.IGNORECASE)
    remaining_content = re.sub(r'<em>(.*?)</em>', r'*\1*', remaining_content, flags=re.DOTALL | re.IGNORECASE)

    # Clean up remaining HTML tags
    remaining_content = re.sub(r'<[^>]+>', ' ', remaining_content)
    remaining_content = re.sub(r'\s+', '\n\n', remaining_content).strip()

    # Add non-empty paragraphs to sections
    for para in paragraphs:
        if para:
            asciidoc_sections.append(para)

    return '\n\n'.join(filter(None, asciidoc_sections)), tables, lists, paragraphs


def convert_html_to_asciidoc(html_path: Path, adoc_path: Path, html_ref_dir: Path) -> bool:
    """Convert a single HTML template to AsciiDoc format."""
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Extract title
        title_match = re.search(r'<title>(.*?)</title>', html_content, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "Legal Document"

        # Parse HTML for AsciiDoc content
        asciidoc_content, tables, lists, paragraphs = parse_html_for_asciidoc(html_content)

        # Create AsciiDoc content with metadata
        adoc_lines = []
        adoc_lines.append(f"# {title}")
        adoc_lines.append("")
        adoc_lines.append(f"* **Original HTML Template:** See `{html_path}`")
        adoc_lines.append("* **Generated:** 2026-08-26")
        adoc_lines.append("")
        adoc_lines.append("toc::")
        adoc_lines.append("")

        # Add HTML comments as reference notes
        html_comments = re.findall(r'<!--(.*?)-->', html_content, re.DOTALL)
        if html_comments:
            adoc_lines.append("====")
            adoc_lines.append("HTML Comments and Metadata:")
            for comment in html_comments[:3]:  # Limit to first 3 comments
                cleaned = re.sub(r'\s+', ' ', comment.strip())
                if cleaned:
                    adoc_lines.append(f"* {cleaned}")
            adoc_lines.append("====")
            adoc_lines.append("")

        # Add main content
        if asciidoc_content:
            adoc_lines.append(asciidoc_content)

        # Add tables if any
        if tables:
            adoc_lines.append("")
            adoc_lines.append("=== Tables ===")
            for i, table in enumerate(tables):
                adoc_lines.append(f"\n*Table {i+1}: Document Structure*')
                adoc_lines.append(table)

        # Add lists if any
        if lists:
            adoc_lines.append("")
            adoc_lines.append("=== Lists ===")
            for i, lst in enumerate(lists):
                adoc_lines.append(f"\n*List {i+1}: Document Components*")
                adoc_lines.append(lst)

        # Write AsciiDoc file
        adoc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(adoc_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(adoc_lines))

        # Save HTML reference
        html_ref_dir.mkdir(parents=True, exist_ok=True)
        ref_filename = f"{html_path.stem}_reference.html"
        ref_path = html_ref_dir / ref_filename

        with open(ref_path, 'w', encoding='utf-8') as f:
            f.write(f"<!DOCTYPE html>\n<html>\n<head>\n<title>HTML Reference: {html_path.name}</title>\n</head>\n<body>\n")
            f.write(f"<h1>HTML Reference for {title}</h1>\n")
            f.write(f"<p><strong>Original File:</strong> {html_path}</p>\n")
            f.write(f"<p><strong>Generated:</strong> 2026-08-26</p>\n")
            f.write("<h2>Full HTML Content:</h2>\n")
            f.write(f"<pre><code>{html_content[:2000]}...</code></pre>\n")
            f.write("</body>\n</html>")

        logger.info(f"✓ Converted {html_path} to {adoc_path}")
        return True

    except Exception as e:
        logger.error(f"✗ Failed to convert {html_path}: {e}")
        return False


def main():
    """Main function to convert HTML templates to AsciiDoc."""
    templates = [
        ("app/adjudication/templates/adjudication/Legal_NonsampleAdjudication_Template.html",
         "app/adjudication/templates/adjudication/Legal_NonsampleAdjudication_Template.adoc"),
        ("app/case_file_generator/templates/case_file_generator/petition.html",
         "app/case_file_generator/templates/case_file_generator/petition.adoc"),
        ("app/case_file_generator/templates/case_file_generator/permission_letter.html",
         "app/case_file_generator/templates/case_file_generator/permission_letter.adoc"),
        ("app/food_cell/templates/food_cell/do_intimation.html",
         "app/food_cell/templates/food_cell/do_intimation.adoc"),
        ("app/food_cell/templates/food_cell/improvement_notice.html",
         "app/food_cell/templates/food_cell/improvement_notice.adoc"),
        ("app/inspection/templates/inspection/edit.html",
         "app/inspection/templates/inspection/edit.adoc"),
        ("app/inspection/templates/inspection/detail.html",
         "app/inspection/templates/inspection/detail.adoc"),
        ("app/case_file_generator/templates/case_file_generator/index.html",
         "app/case_file_generator/templates/case_file_generator/index.adoc"),
        ("app/adjudication/templates/adjudication/index.html",
         "app/adjudication/templates/adjudication/index.adoc"),
    ]

    success_count = 0
    total_count = len(templates)

    for html_path_str, adoc_path_str in templates:
        html_path = Path(html_path_str)
        adoc_path = Path(adoc_path_str)
        html_ref_dir = html_path.parent / "html_references"

        if html_path.exists():
            if convert_html_to_asciidoc(html_path, adoc_path, html_ref_dir):
                success_count += 1
        else:
            logger.warning(f"✗ Source file not found: {html_path}")

    logger.info(f"\nConversion complete: {success_count}/{total_count} templates converted successfully")


if __name__ == "__main__":
    main()