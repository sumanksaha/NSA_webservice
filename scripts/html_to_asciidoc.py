#!/usr/bin/env python3
"""
Convert HTML legal document templates to AsciiDoc format for easier maintenance.

Preserves HTML templates as reference and generates AsciiDoc equivalents.

Author: NSA Webservice Team
Date: 2026-08-26
"""

import logging
import re
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HTMLToAsciiDocConverter:
    def __init__(self, input_path: Path, output_path: Path):
        self.input_path = input_path
        self.output_path = output_path
        self.html_content = ""
        self.title = ""
        self.metadata = {}
        self.sections = []
        self.table_patterns = []
        self.list_patterns = []
        self.paragraphs = []
        self.css_patterns = []
        self.has_scripts = False
        self.has_styles = False

    def parse_html(self) -> None:
        """Parse HTML content and extract structure."""
        with open(self.input_path, encoding="utf-8") as f:
            self.html_content = f.read()

        # Extract title from HTML
        title_match = re.search(r"<title>(.*?)</title>", self.html_content, re.IGNORECASE | re.DOTALL)
        if title_match:
            self.title = title_match.group(1).strip()

        # Extract HTML comments as metadata
        html_comment_matches = re.findall(r"<!--(.*?)-->", self.html_content, re.DOTALL)
        self.metadata["html_comments"] = [c.strip() for c in html_comment_matches if c.strip()]

        # Remove scripts and styles for cleaner content extraction
        self.html_content = re.sub(r"<script.*?</script>", "", self.html_content, flags=re.DOTALL | re.IGNORECASE)
        self.html_content = re.sub(r"<style.*?</style>", "", self.html_content, flags=re.DOTALL | re.IGNORECASE)

        # Convert HTML structure to AsciiDoc
        self._convert_to_asciidoc()

    def _convert_to_asciidoc(self) -> None:
        """Convert HTML content to AsciiDoc format."""
        asciidoc_lines = []

        # Header
        asciidoc_lines.append(f"# {self.title or 'Legal Document'}")

        # Extract metadata from HTML comments and structure
        if self.metadata["html_comments"]:
            asciidoc_lines.append("")
            asciidoc_lines.append("[")
            asciidoc_lines.append("author: NSA Webservice Team")
            asciidoc_lines.append("generated: 2026-08-26")
            asciidoc_lines.append("type: Legal Document Template")
            asciidoc_lines.append("")
            for comment in self.metadata["html_comments"]:
                # Clean up comment for asciidoc
                cleaned_comment = re.sub(r"\s+", " ", comment.strip())
                if cleaned_comment:
                    asciidoc_lines.append(f"* {cleaned_comment}")
            asciidoc_lines.append("]")

        # Process HTML content
        content = self._extract_text_content(self.html_content)

        # Process tables
        tables = self._extract_tables(self.html_content)
        self.table_patterns = tables

        # Process lists
        lists = self._extract_lists(self.html_content)
        self.list_patterns = lists

        # Process paragraphs and headings
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                asciidoc_lines.append("")
                continue

            # Detect and format headings (H1, H2, H3)
            if line.startswith("<h1>"):
                asciidoc_lines.append(f"# {line[4:-5].strip()}")
            elif line.startswith("<h2>"):
                asciidoc_lines.append(f"## {line[4:-5].strip()}")
            elif line.startswith("<h3>"):
                asciidoc_lines.append(f"### {line[4:-5].strip()}")
            elif line.startswith("<h4>"):
                asciidoc_lines.append(f"#### {line[4:-5].strip()}")
            elif line.startswith("<h5>"):
                asciidoc_lines.append(f"##### {line[4:-6].strip()}")
            elif line.startswith("<h6>"):
                asciidoc_lines.append(f"###### {line[4:-5].strip()}")

            # Process bold text
            elif "<b>" in line or "<strong>" in line:
                line = re.sub(r"<(/?)(b|strong)>", r"**\1**", line)
                asciidoc_lines.append(line)

            # Process italic text
            elif "<i>" in line or "<em>" in line:
                line = re.sub(r"<(/?)(i|em)>", r"*\1*", line)
                asciidoc_lines.append(line)

            # Process line breaks
            elif "<br>" in line or "<br/>" in line:
                asciidoc_lines.append(line)

            # Process paragraphs
            elif line.startswith("<p>"):
                para_text = line[3:-4].strip()
                if para_text:
                    asciidoc_lines.append(para_text)
            elif "</p>" in line:
                para_text = line[:-4].strip()
                if para_text:
                    asciidoc_lines.append(para_text)
            elif "<p>" not in line and "</p>" not in line:
                # Regular text
                if not any(tag in line for tag in ["<table>", "<ul>", "<ol>", "<div>", "<span>"]):
                    ascii_line = re.sub(r"<[^>]+>", " ", line)
                    ascii_line = re.sub(r"\s+", " ", ascii_line).strip()
                    if ascii_line:
                        asciidoc_lines.append(ascii_line)

        # Write AsciiDoc content
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(asciidoc_lines))

    def _extract_text_content(self, html: str) -> str:
        """Extract text content from HTML, removing tags."""
        # Remove script and style tags
        text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove all HTML tags but keep content
        text = re.sub(r"<[^>]+>", " ", text)

        # Decode HTML entities
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&amp;", "&")
        text = text.replace("&quot;", '"')
        text = text.replace("&nbsp;", " ")

        # Clean up whitespace
        text = re.sub(r"\s+", "\n", text)
        return text.strip()

    def _extract_tables(self, html: str) -> list[str]:
        """Extract table structures from HTML."""
        tables = []
        table_pattern = r"<table[^>]*>.*?</table>"
        for match in re.finditer(table_pattern, html, re.DOTALL | re.IGNORECASE):
            table_html = match.group(0)

            # Extract table headers
            headers = []
            header_pattern = r"<th[^>]*>(.*?)</th>"
            for header_match in re.finditer(header_pattern, table_html, re.DOTALL | re.IGNORECASE):
                header_text = header_match.group(1).strip()
                headers.append(header_text)

            # Extract table rows
            rows = []
            row_pattern = r"<tr[^>]*>.*?</tr>"
            for row_match in re.finditer(row_pattern, table_html, re.DOTALL | re.IGNORECASE):
                row_html = row_match.group(0)
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL | re.IGNORECASE)
                if cells:
                    rows.append([cell.strip() for cell in cells])

            if headers and rows:
                table_str = "|===\n"
                table_str += "| " + " | ".join(headers) + " |\n"
                table_str += "| " + " | ".join(["---"] * len(headers)) + " |\n"
                for row in rows:
                    table_str += "| " + " | ".join(row) + " |\n"
                table_str += "|==="
                tables.append(table_str)

        return tables

    def _extract_lists(self, html: str) -> list[str]:
        """Extract lists from HTML."""
        lists = []

        # Process unordered lists
        ul_pattern = r"<ul[^>]*>.*?</ul>"
        for match in re.finditer(ul_pattern, html, re.DOTALL | re.IGNORECASE):
            list_html = match.group(0)
            items = re.findall(r"<li[^>]*>(.*?)</li>", list_html, re.DOTALL | re.IGNORECASE)
            if items:
                list_str = ""
                for item in items:
                    cleaned_item = re.sub(r"<[^>]+>", " ", item).strip()
                    list_str += f"* {cleaned_item}\n"
                lists.append(list_str)

        # Process ordered lists
        ol_pattern = r"<ol[^>]*>.*?</ol>"
        for match in re.finditer(ol_pattern, html, re.DOTALL | re.IGNORECASE):
            list_html = match.group(0)
            items = re.findall(r"<li[^>]*>(.*?)</li>", list_html, re.DOTALL | re.IGNORECASE)
            if items:
                list_str = ""
                for i, item in enumerate(items, 1):
                    cleaned_item = re.sub(r"<[^>]+>", " ", item).strip()
                    list_str += f"{i}. {cleaned_item}\n"
                lists.append(list_str)

        return lists

    def save_original_reference(self) -> None:
        """Save the original HTML as a reference file."""
        ref_dir = self.input_path.parent / "html_references"
        ref_dir.mkdir(parents=True, exist_ok=True)

        # Create a reference file with metadata
        ref_content = f"""HTML Reference for AsciiDoc Template
=========================================

Original File: {self.input_path.name}
Converted: 2026-08-26

HTML Structure:
---------------
{self.html_content[:2000]}...

Converted AsciiDoc available at: {self.output_path.name}
"""

        ref_path = ref_dir / f"{self.input_path.stem}_reference.html"
        with open(ref_path, "w", encoding="utf-8") as f:
            f.write(ref_content)

    def convert(self) -> None:
        """Convert HTML to AsciiDoc and save reference."""
        logger.info(f"Converting {self.input_path} to AsciiDoc...")

        # Parse HTML and extract structure
        self.parse_html()

        # Save converted AsciiDoc
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write(
                "\n".join([
                    f"# {self.title}",
                    "",
                    f"* **Original HTML Template:** See `{self.input_path}`",
                    "* **Generated:** 2026-08-26",
                    "",
                    *self._generate_asciidoc_content(),
                ])
            )

        # Save original HTML as reference
        self.save_original_reference()

        logger.info(f"Successfully converted to {self.output_path}")

    def _generate_asciidoc_content(self) -> list[str]:
        """Generate the main AsciiDoc content."""
        lines = []

        # Add table of contents
        lines.append("toc::")
        lines.append("")

        # Add content sections
        for section in self.sections:
            lines.append(section)
            lines.append("")

        # Add tables if any
        for i, table in enumerate(self.table_patterns):
            lines.append(f"\n*Table {i + 1}: Document Structure*")
            lines.append(table)

        # Add lists if any
        for i, lst in enumerate(self.list_patterns):
            lines.append(f"\n*List {i + 1}: Document Components*")
            lines.append(lst)

        # Add paragraphs
        for para in self.paragraphs:
            lines.append(para)

        return lines


def main():
    """Main function to convert HTML templates to AsciiDoc."""
    # Define HTML templates to convert
    templates = [
        (
            "app/adjudication/templates/adjudication/Legal_NonsampleAdjudication_Template.html",
            "app/adjudication/templates/adjudication/Legal_NonsampleAdjudication_Template.adoc",
        ),
        (
            "app/case_file_generator/templates/case_file_generator/petition.html",
            "app/case_file_generator/templates/case_file_generator/petition.adoc",
        ),
        (
            "app/case_file_generator/templates/case_file_generator/permission_letter.html",
            "app/case_file_generator/templates/case_file_generator/permission_letter.adoc",
        ),
        (
            "app/food_cell/templates/food_cell/do_intimation.html",
            "app/food_cell/templates/food_cell/do_intimation.adoc",
        ),
        (
            "app/food_cell/templates/food_cell/improvement_notice.html",
            "app/food_cell/templates/food_cell/improvement_notice.adoc",
        ),
        ("app/inspection/templates/inspection/edit.html", "app/inspection/templates/inspection/edit.adoc"),
        ("app/inspection/templates/inspection/detail.html", "app/inspection/templates/inspection/detail.adoc"),
        (
            "app/case_file_generator/templates/case_file_generator/index.html",
            "app/case_file_generator/templates/case_file_generator/index.adoc",
        ),
        ("app/adjudication/templates/adjudication/index.html", "app/adjudication/templates/adjudication/index.adoc"),
    ]

    for html_path, adoc_path in templates:
        full_html_path = Path(html_path)
        if full_html_path.exists():
            converter = HTMLToAsciiDocConverter(full_html_path, Path(adoc_path))
            converter.convert()
            print(f"Converted {html_path} to {adoc_path}")
        else:
            print(f"Warning: {html_path} not found")

    print("\nAsciiDoc conversion complete!")
    print("Original HTML templates are preserved in app/*/templates/*/html_references/")


if __name__ == "__main__":
    main()
