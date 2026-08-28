"""Word (.docx) converter for Improvement Notices (u/s 32, FSS Act).

Builds a professional government-style Word document from the same context
data used by the HTML template.  The output is suitable for email attachment
and official printing.

Usage::

    from app.food_cell.word_converter import ImprovementNoticeWordConverter

    converter = ImprovementNoticeWordConverter()
    docx_bytes = converter.build(context_dict)
"""

from __future__ import annotations

import io
import logging
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

logger = logging.getLogger(__name__)

# ── Colour palette (matches the HTML template) ──────────────────────────
NAVY = RGBColor(0x1A, 0x3A, 0x6B)
DARK_GRAY = RGBColor(0x33, 0x33, 0x33)
MEDIUM_GRAY = RGBColor(0x55, 0x55, 0x55)
LIGHT_GRAY = RGBColor(0x88, 0x88, 0x88)
HEADER_BG = "1A3A6B"
ALT_ROW_BG = "F8F9FB"
SUBJECT_BG = "F0F4FA"
COMPLIANCE_BG = "FEF9E7"
WHITE = RGBColor(0xFF, 0xFF, 0xFF)


class ImprovementNoticeWordConverter:
    """Build a .docx Improvement Notice from a Jinja2-style context dict.

    Expected keys (same as the HTML template):
        fbo_name, fbo_address, inspection_date, fbo_fssai, fso_name,
        notice_date, improvement_notice_ref, violations (list of dicts),
        actions (list of str), compliance_deadline, enclosures (list of str).
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def build(self, context: dict[str, Any]) -> bytes:
        """Return the .docx file as *bytes*."""
        # Auto-resolve signature image if not already provided
        if "_signature_bytes" not in context:
            from app.food_cell.signature_resolver import get_signature_bytes

            sig = get_signature_bytes(context.get("fso_name"))
            if sig:
                context = {**context, "_signature_bytes": sig}

        doc = self._create_document()
        self._add_letterhead(doc)
        self._add_doc_class_badge(doc)
        self._add_notice_date(doc, context)
        self._add_recipient(doc)
        self._add_body(doc, context)
        self._add_footer(doc, context)

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _create_document(self) -> Document:
        doc = Document()
        # Narrow margins for an official letter
        for section in doc.sections:
            section.top_margin = Cm(2.5)
            section.bottom_margin = Cm(2.0)
            section.left_margin = Cm(2.5)
            section.right_margin = Cm(2.5)
        # Set default font
        style = doc.styles["Normal"]
        font = style.font
        font.name = "Arial"
        font.size = Pt(11)
        font.color.rgb = DARK_GRAY
        return doc

    # ── Letterhead ──────────────────────────────────────────────────────

    def _add_letterhead(self, doc: Document) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run("KOLKATA MUNICIPAL CORPORATION")
        run.font.size = Pt(8)
        run.font.color.rgb = NAVY
        run.bold = True
        # Letter-spacing via XML
        self._set_char_spacing(run, 60)

        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r2 = p2.add_run("Food Safety Department")
        r2.font.size = Pt(15)
        r2.font.color.rgb = NAVY
        r2.bold = True

        p3 = doc.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r3 = p3.add_run("Under Food Safety and Standards Act, 2006")
        r3.font.size = Pt(10)
        r3.font.color.rgb = MEDIUM_GRAY
        r3.italic = True

        # Navy bottom border
        self._add_bottom_border(doc, color=HEADER_BG, width=12)

    # ── Document class badge ────────────────────────────────────────────

    def _add_doc_class_badge(self, doc: Document) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.space_before = Pt(12)
        run = p.add_run(
            "  IMPROVEMENT NOTICE — SECTION 32, FSS ACT, 2006  "
        )
        run.font.size = Pt(8)
        run.font.color.rgb = WHITE
        run.bold = True
        self._set_char_spacing(run, 20)
        # Shading behind the run
        self._shade_paragraph(p, HEADER_BG)

    # ── Notice date ─────────────────────────────────────────────────────

    def _add_notice_date(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(6)
        run = p.add_run(ctx.get("notice_date") or "—")
        run.font.size = Pt(9.5)
        run.font.color.rgb = DARK_GRAY
        self._add_bottom_border(doc, color="D0D0D0", width=4)

    # ── Recipient ───────────────────────────────────────────────────────

    def _add_recipient(self, doc: Document) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(8)
        # "TO" label
        lbl = p.add_run("TO\n")
        lbl.font.size = Pt(8)
        lbl.font.color.rgb = LIGHT_GRAY
        lbl.bold = True
        self._set_char_spacing(lbl, 20)

        body = p.add_run(
            "The Designated Officer,\nFood Cell,\nKolkata Municipal Corporation."
        )
        body.font.size = Pt(10)
        body.font.color.rgb = DARK_GRAY
        # Left border on paragraph
        self._set_left_border(p, color=HEADER_BG, width=12)

    # ── Body ────────────────────────────────────────────────────────────

    def _add_body(self, doc: Document, ctx: dict[str, Any]) -> None:
        # Reference badge
        ref = ctx.get("improvement_notice_ref")
        if ref:
            p = doc.add_paragraph()
            p.space_before = Pt(8)
            run = p.add_run(f"Ref: {ref}")
            run.font.size = Pt(10)
            run.font.color.rgb = NAVY
            run.bold = True
            self._shade_paragraph(p, "EEF2F8")
            self._set_left_border(p, color=HEADER_BG, width=4)

        # Subject
        self._add_section_heading(doc, "Subject")
        fbo = ctx.get("fbo_name") or "[FBO Name]"
        addr = ctx.get("fbo_address") or "[FBO Address]"
        date = ctx.get("inspection_date") or "[Inspection Date]"
        p = doc.add_paragraph()
        p.space_before = Pt(4)
        run = p.add_run(
            f"Inspection report regarding an inspection of {fbo} "
            f"situated at {addr} on {date}."
        )
        run.bold = True
        run.font.size = Pt(10.5)
        self._shade_paragraph(p, SUBJECT_BG)
        self._set_left_border(p, color=HEADER_BG, width=12)

        # Salutation
        p = doc.add_paragraph()
        p.space_before = Pt(8)
        p.add_run("Sir/Madam,").font.size = Pt(11)

        # ── FBO summary table ──────────────────────────────────────────
        if ctx.get("fbo_name") or ctx.get("fbo_fssai"):
            self._add_section_heading(doc, "DETAILS OF INSPECTION")
            rows_data = []
            if ctx.get("fbo_name"):
                rows_data.append(("FBO Name", ctx["fbo_name"]))
            if ctx.get("fbo_address"):
                rows_data.append(("Address", ctx["fbo_address"]))
            if ctx.get("fbo_fssai"):
                rows_data.append(("FSSAI License No.", ctx["fbo_fssai"]))
            if ctx.get("inspection_date"):
                rows_data.append(("Inspection Date", ctx["inspection_date"]))
            if ctx.get("fso_name"):
                rows_data.append(("Food Safety Officer", ctx["fso_name"]))
            if rows_data:
                self._add_kv_table(doc, rows_data)

        # ── Part 1: Inspection Findings ────────────────────────────────
        self._add_section_heading(doc, "PART 1 — INSPECTION FINDINGS")
        p = doc.add_paragraph()
        p.space_before = Pt(4)
        run = p.add_run("An inspection was performed at ")
        run.font.size = Pt(11)
        r_bold = p.add_run(f"{addr}")
        r_bold.bold = True
        r_bold.font.size = Pt(11)
        r_mid = p.add_run(" on ")
        r_mid.font.size = Pt(11)
        r_date = p.add_run(f"{date}")
        r_date.bold = True
        r_date.font.size = Pt(11)
        r_end = p.add_run(", and the following deviation was observed.")
        r_end.font.size = Pt(11)

        # Violations table
        violations = ctx.get("violations") or []
        if violations:
            self._add_violations_table(doc, violations)
        else:
            p = doc.add_paragraph()
            run = p.add_run("No specific deviations were recorded during the inspection.")
            run.italic = True

        # ── Part 2: Grounds ────────────────────────────────────────────
        self._add_section_heading(doc, "PART 2 — GROUNDS FOR IMPROVEMENT NOTICE")
        p = doc.add_paragraph()
        p.space_before = Pt(4)
        p.add_run(
            "Based on the following observations, an improvement notice "
            "u/s 32 may kindly be granted on the following ground:"
        ).font.size = Pt(11)

        actions = ctx.get("actions") or []
        if actions:
            self._add_actions_list(doc, actions)
        else:
            p = doc.add_paragraph()
            run = p.add_run("No specific remedial actions prescribed.")
            run.italic = True

        # ── Compliance deadline ─────────────────────────────────────────
        deadline = ctx.get("compliance_deadline")
        if deadline:
            p = doc.add_paragraph()
            p.space_before = Pt(8)
            p.add_run(
                "The FBO is hereby directed to comply with the above observations "
                "and take the required corrective action on or before "
            ).font.size = Pt(10.5)
            r = p.add_run(f"{deadline}.")
            r.bold = True
            r.font.size = Pt(10.5)
            self._shade_paragraph(p, COMPLIANCE_BG)
            self._set_left_border(p, color="D4A017", width=12)

        # ── Enclosures ─────────────────────────────────────────────────
        enclosures = ctx.get("enclosures") or []
        if enclosures:
            p = doc.add_paragraph()
            p.space_before = Pt(8)
            r = p.add_run("Enclosures:")
            r.bold = True
            r.font.size = Pt(10)
            for enc in enclosures:
                p = doc.add_paragraph(style="List Number")
                p.add_run(enc).font.size = Pt(10)

        # ── Signature block ────────────────────────────────────────────
        self._add_signature_block(doc, ctx)

    # ── Tables ──────────────────────────────────────────────────────────

    def _add_kv_table(
        self, doc: Document, rows: list[tuple[str, str]]
    ) -> None:
        table = doc.add_table(rows=len(rows), cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        for i, (key, val) in enumerate(rows):
            # Key cell
            cell_k = table.cell(i, 0)
            cell_k.width = Cm(4.5)
            p = cell_k.paragraphs[0]
            run = p.add_run(key)
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = NAVY
            self._shade_cell(cell_k, "E8ECF2")

            # Value cell
            cell_v = table.cell(i, 1)
            p = cell_v.paragraphs[0]
            run = p.add_run(val)
            run.font.size = Pt(10)

            # Alternating row shading
            if i % 2 == 1:
                self._shade_cell(cell_v, ALT_ROW_BG)

        self._style_table_borders(table, color="C0C8D4")

    def _add_violations_table(
        self, doc: Document, violations: list[dict[str, str]]
    ) -> None:
        table = doc.add_table(rows=1 + len(violations), cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        # Header row
        headers = ["Sl.", "Nature of Deviation", "Observation"]
        widths = [Cm(1.2), Cm(5.0), Cm(9.0)]
        for j, hdr in enumerate(headers):
            cell = table.cell(0, j)
            cell.width = widths[j]
            p = cell.paragraphs[0]
            run = p.add_run(hdr)
            run.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = WHITE
            self._shade_cell(cell, HEADER_BG)

        # Data rows
        for i, v in enumerate(violations):
            row_idx = i + 1
            # Sl.
            cell = table.cell(row_idx, 0)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run(str(i + 1)).font.size = Pt(10)

            # Title / Nature of Deviation
            cell = table.cell(row_idx, 1)
            p = cell.paragraphs[0]
            p.add_run(v.get("title") or v.get("description") or v.get("name", "")).font.size = Pt(10)

            # Observation / Section
            cell = table.cell(row_idx, 2)
            p = cell.paragraphs[0]
            p.add_run(v.get("observation") or v.get("section") or v.get("detail", "")).font.size = Pt(10)

            # Alternating row
            if row_idx % 2 == 0:
                for j in range(3):
                    self._shade_cell(table.cell(row_idx, j), ALT_ROW_BG)

        self._style_table_borders(table, color="C0C8D4")

    def _add_actions_list(
        self, doc: Document, actions: list[str]
    ) -> None:
        for i, action in enumerate(actions):
            p = doc.add_paragraph()
            p.space_before = Pt(2)
            p.space_after = Pt(4)
            # Number
            r_num = p.add_run(f"{i + 1}.  ")
            r_num.bold = True
            r_num.font.size = Pt(11)
            r_num.font.color.rgb = NAVY
            # Text
            p.add_run(action).font.size = Pt(11)

    # ── Section heading ─────────────────────────────────────────────────

    def _add_section_heading(self, doc: Document, text: str) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(14)
        p.space_after = Pt(4)
        run = p.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(10)
        run.font.color.rgb = NAVY
        self._set_char_spacing(run, 10)
        self._add_bottom_border(doc, color=HEADER_BG, width=6)

    # ── Signature block ─────────────────────────────────────────────────

    def _add_signature_block(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(36)
        lbl = p.add_run("ISSUED BY\n")
        lbl.font.size = Pt(8)
        lbl.font.color.rgb = LIGHT_GRAY
        self._set_char_spacing(lbl, 20)

        self._add_bottom_border(doc, color=HEADER_BG, width=4)

        # Embed signature image if available
        sig_bytes = ctx.get("_signature_bytes")
        if sig_bytes:
            sig_stream = io.BytesIO(sig_bytes)
            doc.add_picture(sig_stream, width=Inches(1.5))

        p2 = doc.add_paragraph()
        p2.space_before = Pt(4)
        r = p2.add_run(f"{ctx.get('fso_name') or '[FSO Name]'}\n")
        r.bold = True
        r.font.size = Pt(11)
        p2.add_run("Food Safety Officer\n").font.size = Pt(11)
        p2.add_run("Kolkata Municipal Corporation\n").font.size = Pt(11)
        p2.add_run(ctx.get("notice_date") or "—").font.size = Pt(11)

    # ── Footer ──────────────────────────────────────────────────────────

    def _add_footer(self, doc: Document, ctx: dict[str, Any]) -> None:
        section = doc.sections[0]
        footer = section.footer
        footer.is_linked_to_previous = False
        p = footer.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run("This document is electronically generated.")
        run.font.size = Pt(8)
        run.font.color.rgb = LIGHT_GRAY
        ref = ctx.get("improvement_notice_ref")
        if ref:
            p.add_run(f"    Ref: {ref}").font.size = Pt(8)

    # ── Low-level formatting utilities ──────────────────────────────────

    @staticmethod
    def _set_char_spacing(run: Any, hundredths: int) -> None:
        """Set character spacing in hundredths of a point."""
        rpr = run._r.get_or_add_rPr()
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:val"), str(hundredths))
        rpr.append(spacing)

    @staticmethod
    def _shade_paragraph(paragraph: Any, fill_hex: str) -> None:
        """Apply background shading to a paragraph."""
        pPr = paragraph._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill_hex)
        pPr.append(shd)

    @staticmethod
    def _shade_cell(cell: Any, fill_hex: str) -> None:
        """Apply background shading to a table cell."""
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill_hex)
        tcPr.append(shd)

    @staticmethod
    def _set_left_border(
        paragraph: Any, color: str = "1A3A6B", width: int = 12
    ) -> None:
        """Set a left border on a paragraph."""
        pPr = paragraph._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        left = OxmlElement("w:left")
        left.set(qn("w:val"), "single")
        left.set(qn("w:sz"), str(width))
        left.set(qn("w:space"), "4")
        left.set(qn("w:color"), color)
        pBdr.append(left)
        pPr.append(pBdr)

    @staticmethod
    def _add_bottom_border(
        doc: Document, color: str = "1A3A6B", width: int = 8
    ) -> None:
        """Add a thin bottom border via a new paragraph with bottom border."""
        p = doc.add_paragraph()
        p.space_before = Pt(2)
        p.space_after = Pt(2)
        pPr = p._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(width))
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), color)
        pBdr.append(bottom)
        pPr.append(pBdr)

    @staticmethod
    def _style_table_borders(table: Any, color: str = "C0C8D4") -> None:
        """Apply uniform borders to a table."""
        tbl = table._tbl
        tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement("w:tblPr")
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), color)
            borders.append(el)
        tblPr.append(borders)


__all__ = ["ImprovementNoticeWordConverter"]
