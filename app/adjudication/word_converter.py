"""Word (.docx) converter for Adjudication documents (Petition + Permission Letter).

Builds professional government-style Word documents from the same context
data used by the HTML templates.  The output is suitable for email attachment
and official printing.

Usage::

    from app.adjudication.word_converter import AdjudicationWordConverter

    converter = AdjudicationWordConverter()
    petition_docx = converter.build_petition(context_dict)
    permission_docx = converter.build_permission_letter(context_dict)
"""

from __future__ import annotations

import io
import logging
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

logger = logging.getLogger(__name__)

# ── Colour palette ─────────────────────────────────────────────────
NAVY = RGBColor(0x1A, 0x3A, 0x6B)
DARK_GRAY = RGBColor(0x33, 0x33, 0x33)
MEDIUM_GRAY = RGBColor(0x55, 0x55, 0x55)
LIGHT_GRAY = RGBColor(0x88, 0x88, 0x88)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
HEADER_BG = "1A3A6B"
ALT_ROW_BG = "F8F9FB"
LEGAL_BOX_BG = "F8F9FA"

# ── Section liability texts ────────────────────────────────────────
_SECTION_LIABILITY = {
    "55": (
        "If a food business operator or an importer without reasonable ground, "
        "fails to comply with the requirements of this Act or the rules or "
        "regulations or orders issued thereunder, as directed by the Food Safety "
        "Officer, he shall be liable to a penalty which may extend to two lakh rupees."
    ),
    "56": (
        "Any person who, whether by himself or by any other person on his behalf, "
        "manufactures or processes any article of food for human consumption under "
        "unhygienic or unsanitary conditions, shall be liable to a penalty which "
        "may extend to one lakh rupees."
    ),
    "58": (
        "Any person who whether by himself or by any other person on his behalf "
        "manufactures for sale or stores or sells or distributes or imports any "
        "article of food for human consumption which is unsafe shall be liable to "
        "a penalty which may extend to one lakh rupees."
    ),
    "63": (
        "Any person who whether by himself or by any other person on his behalf "
        "manufactures or processes any article of food for human consumption "
        "without a license shall be liable to a penalty which may extend to "
        "five lakh rupees."
    ),
    "64": (
        "Any person who publishes or is a party to the publication of any "
        "advertisement which is false or misleading shall be liable to a penalty "
        "which may extend to ten lakh rupees."
    ),
}


class AdjudicationWordConverter:
    """Build .docx Adjudication documents from a Jinja2-style context dict.

    Expected keys (same as the HTML templates):
        case_number, food_safety_officer_name, authorization_date,
        first_inspection_date, compliance_deadline, followup_inspection_date,
        complaint_date, complaint_lodged, concerned_food, problem,
        fbo_owner, fbo_name, fbo_address, fssai_license,
        ce_license_no, non_license, pre_authorization,
        violations (list of dicts with title/observation),
        applicable_sections, sections_display, compilation_date.
    """

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def build_petition(self, context: dict[str, Any]) -> bytes:
        """Return the Petition .docx as *bytes*."""
        doc = self._create_document()
        self._add_petition_header(doc, context)
        self._add_parties(doc, context)
        self._page_break(doc)
        self._add_petition_body(doc, context)
        self._add_prayer(doc, context)
        self._add_signature_footer(doc, context)
        self._add_to_do(doc, context)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    def build_permission_letter(self, context: dict[str, Any]) -> bytes:
        """Return the Permission Letter .docx as *bytes*."""
        doc = self._create_document()
        self._add_permission_letter(doc, context)
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return buf.read()

    # ------------------------------------------------------------------ #
    # Document setup
    # ------------------------------------------------------------------ #

    def _create_document(self) -> Document:
        doc = Document()
        for section in doc.sections:
            section.top_margin = Cm(2.5)
            section.bottom_margin = Cm(2.0)
            section.left_margin = Cm(2.5)
            section.right_margin = Cm(2.5)
        style = doc.styles["Normal"]
        font = style.font
        font.name = "Times New Roman"
        font.size = Pt(12)
        font.color.rgb = DARK_GRAY
        return doc

    # ------------------------------------------------------------------ #
    # Petition
    # ------------------------------------------------------------------ #

    def _add_petition_header(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        run = p.add_run("Before the Ld. Adjudicating Officer, KMC")
        run.bold = True
        run.font.size = Pt(12)

        p2 = doc.add_paragraph()
        p2.add_run("F.S. & S Case No. ")

        p3 = doc.add_paragraph()
        p3.space_before = Pt(6)
        r3 = p3.add_run("Complainant/Food Business Operator with Address\nCompany/Firm/Proprietor")
        r3.bold = True

    def _add_parties(self, doc: Document, ctx: dict[str, Any]) -> None:
        table = doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        # Left — Complainant
        cell_l = table.cell(0, 0)
        cell_l.width = Cm(8)
        p = cell_l.paragraphs[0]
        r = p.add_run(
            f"1. {ctx.get('food_safety_officer_name', '')}\n"
            f"Food Safety Officer\n"
            f"Kolkata Municipal Corporation"
        )
        r.font.size = Pt(10)

        p2 = cell_l.add_paragraph()
        r2 = p2.add_run(
            "2. License Officer\n"
            "5, S.N. Banerjee Road, Kolkata - 700013"
        )
        r2.font.size = Pt(10)

        # Right — Respondent
        cell_r = table.cell(0, 1)
        cell_r.width = Cm(8)
        p = cell_r.paragraphs[0]
        r = p.add_run(
            f"{ctx.get('fbo_owner', '')}\n"
            f"{ctx.get('fbo_name', '')}\n"
            f"{ctx.get('fbo_address', '')}\n"
        )
        r.font.size = Pt(10)
        if ctx.get("non_license") == "yes" or "63" in (ctx.get("applicable_sections") or []):
            p.add_run(f"Trade License No: {ctx.get('ce_license_no', '')}").font.size = Pt(10)
        else:
            p.add_run(f"{ctx.get('fssai_license', '')}").font.size = Pt(10)

        self._style_table_borders(table)

    def _add_petition_body(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(12)
        r = p.add_run("Most Respectfully Sheweth: -")
        r.bold = True

        p2 = doc.add_paragraph()
        p2.add_run("The humble petition on behalf of the complainant above named")

        # STATEMENT OF FACTS
        self._add_section_heading(doc, "STATEMENT OF FACTS")
        facts = self._build_facts(ctx)
        for i, fact in enumerate(facts, 1):
            p = doc.add_paragraph()
            r_num = p.add_run(f"{i}. ")
            r_num.bold = True
            p.add_run(fact).font.size = Pt(12)

        # Violations table
        violations = ctx.get("violations", [])
        if violations:
            p = doc.add_paragraph()
            p.space_before = Pt(8)
            r = p.add_run("Statement of Observed Non-Compliances During Inspection")
            r.bold = True
            self._add_violations_table(doc, violations)

        # Legal notice box
        self._add_legal_notice_box(doc, ctx)

    def _build_facts(self, ctx: dict[str, Any]) -> list[str]:
        sections_display = ctx.get("sections_display", "___")
        non_license = ctx.get("non_license", "no")
        applicable = ctx.get("applicable_sections") or []
        license_text = (
            f"Trade License No- {ctx.get('ce_license_no', '')}"
            if non_license == "yes" or "63" in applicable
            else f"License/Registration No- {ctx.get('fssai_license', '')}"
        )

        facts = [
            (
                f'That the complainant above named is the Food safety officer '
                f'(hereinafter referred to as the "FSO") appointed u/s 37 of the '
                f'Food Safety and Standards Act, 2006 for whole area of Kolkata '
                f'under Kolkata Municipal Corporation and he has been authorised by '
                f'the Designated Officer, Kolkata Municipal Corporation dated '
                f'{ctx.get("authorization_date", "")} to launch the present complaint '
                f'for adjudication before the Ld. Adjudicating Officer, Kolkata '
                f'Municipal Corporation.'
            ),
            (
                f'That on {ctx.get("first_inspection_date", "")}, FSO inspected the '
                f'premises of {ctx.get("fbo_name", "")} (hereinafter referred to as '
                f'the "FBO") situated at {ctx.get("fbo_address", "")}, {license_text}, '
                f'where he found that said business was operated by '
                f'{ctx.get("fbo_owner", "")}, who also disclosed that he was looking '
                f'after the day to day affairs of the FBO including storage and selling '
                f'of food articles.'
            ),
            "That on inspection, FSO observed several discrepancies at the premises.",
        ]

        # Add observation items from violations
        violations = ctx.get("violations", [])
        for v in violations:
            title = v.get("title", "")
            obs = v.get("observation", "")
            facts.append(f"{title}: {obs}")

        facts.append(
            f"The undersigned issued an instruction sheet to the FBO to address the "
            f"non-compliance issues on or before {ctx.get('compliance_deadline', '')}."
        )

        if ctx.get("complaint_lodged") == "yes":
            facts.append(
                f"Subsequently, on {ctx.get('complaint_date', '')}, the FSO received a "
                f"complaint regarding the sale of {ctx.get('concerned_food', '')} that "
                f"{ctx.get('problem', '')}, further corroborating the FBO's continued "
                f"non-compliance."
            )

        facts.extend([
            (
                f'That on {ctx.get("followup_inspection_date", "")}, the FSO conducted '
                f'a follow-up inspection and observed continued non-compliance with food '
                f'safety standards and with directives issued by the FSO.'
            ),
            (
                f'That Food Safety Officer submitted all the relevant documents in '
                f'connection with above FBO and placed before the Designated Officer '
                f'for his perusal and further proceedings.'
            ),
            (
                f'That the Designated Officer authorized Food Safety Officer on '
                f'{ctx.get("authorization_date", "")} to file an application for '
                f'Adjudication with the Adjudicating Officer.'
            ),
        ])
        return facts

    def _add_prayer(self, doc: Document, ctx: dict[str, Any]) -> None:
        sections_display = ctx.get("sections_display", "___")
        sections = ctx.get("applicable_sections") or []
        plural = "s" if len(sections) > 1 else ""

        p = doc.add_paragraph()
        p.space_before = Pt(12)
        p.add_run(
            f"Under the circumstances, it is prayed that Your Honour may kindly be "
            f"pleased to issue notice upon the FBO to make representation and fix a "
            f"date of hearing and after hearing pass necessary order/ orders "
            f"contravening Section{plural} {sections_display} of the Food Safety and "
            f"Standards Act, 2006, and pass such order or orders as deemed fit and "
            f"proper in the interest of justice."
        )

        p2 = doc.add_paragraph()
        p2.add_run("And for this act of kindness your petitioner as in duty bound shall ever pray.")

    def _add_to_do(self, doc: Document, ctx: dict[str, Any]) -> None:
        """The 'To' letter appended after the petition."""
        doc.add_page_break()

        p = doc.add_paragraph()
        p.space_before = Pt(20)
        r = p.add_run("To\nThe Designated Officer,\nFood Cell,\nKolkata Municipal Corporation.")
        r.bold = True

        p2 = doc.add_paragraph()
        p2.space_before = Pt(20)
        r2 = p2.add_run("Re : Filing of Complaint before the Ld. Adjudicating Officer")
        r2.bold = True
        r2.underline = True

        p3 = doc.add_paragraph()
        p3.space_before = Pt(12)
        p3.add_run(
            f"In view of your approval for initiation of adjudication proceedings "
            f"dated {ctx.get('authorization_date', '')}, the prepared draft copy of "
            f"the petition of complaint is hereby placed before you for your kind "
            f"perusal and necessary approval, please."
        )

        p4 = doc.add_paragraph()
        p4.space_before = Pt(60)
        p4.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p4.add_run("Food Safety Officer\nFood Cell\nKolkata Municipal Corporation")

    # ------------------------------------------------------------------ #
    # Permission Letter
    # ------------------------------------------------------------------ #

    def _add_permission_letter(self, doc: Document, ctx: dict[str, Any]) -> None:
        p_date = doc.add_paragraph()
        p_date.add_run(f"Date: {ctx.get('compilation_date', '')}")

        p_to = doc.add_paragraph()
        p_to.space_before = Pt(12)
        r = p_to.add_run("To\nThe Designated Officer,\nFood Cell,\nKolkata Municipal Corporation.")
        r.bold = True

        sections = ctx.get("applicable_sections") or []
        sections_display = ctx.get("sections_display", "___")
        plural = "s" if len(sections) > 1 else ""

        p_sub = doc.add_paragraph()
        p_sub.space_before = Pt(12)
        r = p_sub.add_run("Subject: ")
        r.bold = True
        r.underline = True
        p_sub.add_run(
            f"Prayer for authorization for filing of adjudication proceedings "
            f"against {ctx.get('fbo_name', '')}, situated at "
            f"{ctx.get('fbo_address', '')} under Section{plural} "
            f"{sections_display} of the Food Safety and Standards Act, 2006."
        )

        p_sal = doc.add_paragraph()
        p_sal.add_run("Sir/Madam,")

        p1 = doc.add_paragraph()
        p1.space_before = Pt(8)
        p1.add_run(
            f"During inspection conducted on {ctx.get('first_inspection_date', '')} "
            f"at the food business establishment namely {ctx.get('fbo_name', '')}, "
            f"situated at {ctx.get('fbo_address', '')}, the following non-compliances "
            f"and contraventions of food hygiene and sanitary requirements prescribed "
            f"under the Food Safety and Standards Act, 2006 and allied Regulations were "
            f"observed:"
        )

        # Violations table
        violations = ctx.get("violations", [])
        if violations:
            self._add_violations_table(doc, violations)

        p2 = doc.add_paragraph()
        p2.space_before = Pt(8)
        p2.add_run(
            f"The undersigned issued an instruction sheet to the FBO to address the "
            f"non-compliance issues on or before {ctx.get('compliance_deadline', '')}."
        )

        if ctx.get("complaint_lodged") == "yes":
            p3 = doc.add_paragraph()
            p3.add_run(
                f"Subsequently, on {ctx.get('complaint_date', '')}, the FSO received a "
                f"complaint from the complainant regarding the sale of a "
                f"{ctx.get('concerned_food', '')} that {ctx.get('problem', '')}, further "
                f"corroborating the FBO's continued non-compliance."
            )

        p4 = doc.add_paragraph()
        p4.add_run(
            f"That a follow-up inspection was conducted on "
            f"{ctx.get('followup_inspection_date', '')} and the FSO observed continued "
            f"non-compliance with food safety standards and with directives issued by "
            f"the FSO."
        )

        p5 = doc.add_paragraph()
        p5.space_before = Pt(12)
        p5.add_run(
            f"In view of the above findings and observed contraventions, it is "
            f"respectfully prayed that necessary permission may kindly be accorded for "
            f"initiation and filing of adjudication proceedings before the Learned "
            f"Adjudicating Officer under Section{plural} {sections_display} of the Food "
            f"Safety and Standards Act, 2006, as deemed fit and proper in the interest "
            f"of food safety and public health."
        )

        # Signature
        p_sig = doc.add_paragraph()
        p_sig.space_before = Pt(30)
        p_sig.add_run(f"Date: {ctx.get('compilation_date', '')}")

        p_sig2 = doc.add_paragraph()
        p_sig2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_sig2.add_run("Signature of Food Safety Officer: __________________________\n")
        r = p_sig2.add_run("(Food Safety Officer)")
        r.font.size = Pt(10)

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _add_violations_table(self, doc: Document, violations: list[dict]) -> None:
        table = doc.add_table(rows=1 + len(violations), cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        headers = ["Sl.", "Nature of Non-Compliance", "Observation"]
        widths = [Cm(1.2), Cm(5.0), Cm(9.0)]
        for j, hdr in enumerate(headers):
            cell = table.cell(0, j)
            cell.width = widths[j]
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(hdr)
            run.bold = True
            run.font.size = Pt(9)
            run.font.color.rgb = WHITE
            self._shade_cell(cell, HEADER_BG)

        for i, v in enumerate(violations):
            row_idx = i + 1
            # Sl.
            cell = table.cell(row_idx, 0)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.add_run(str(i + 1)).font.size = Pt(10)

            # Title
            cell = table.cell(row_idx, 1)
            p = cell.paragraphs[0]
            p.add_run(v.get("title", "")).font.size = Pt(10)

            # Observation
            cell = table.cell(row_idx, 2)
            p = cell.paragraphs[0]
            p.add_run(v.get("observation", "")).font.size = Pt(10)

            if row_idx % 2 == 0:
                for j in range(3):
                    self._shade_cell(table.cell(row_idx, j), ALT_ROW_BG)

        self._style_table_borders(table)

    def _add_legal_notice_box(self, doc: Document, ctx: dict[str, Any]) -> None:
        sections = ctx.get("applicable_sections", [])
        if not sections:
            return

        for sec in sections:
            p2 = doc.add_paragraph()
            p2.space_before = Pt(8)
            r = p2.add_run(f"Liability under Section {sec}:")
            r.bold = True
            r.underline = True
            r.font.size = Pt(11)
            r.font.color.rgb = NAVY
            self._shade_paragraph(p2, LEGAL_BOX_BG)

            text = _SECTION_LIABILITY.get(sec, f"Liability provisions for Section {sec} apply.")
            p3 = doc.add_paragraph()
            r3 = p3.add_run(text)
            r3.font.size = Pt(11)
            r3.italic = True
            self._set_left_border(p3, color="D0D0D0", width=4)

    def _add_signature_footer(self, doc: Document, ctx: dict[str, Any]) -> None:
        table = doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        cell_l = table.cell(0, 0)
        p = cell_l.paragraphs[0]
        p.add_run("Designated Officer\nKolkata Municipal Corporation").font.size = Pt(10)

        cell_r = table.cell(0, 1)
        p2 = cell_r.paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p2.add_run("Food Safety Officer\nKolkata Municipal Corporation").font.size = Pt(10)

    # ------------------------------------------------------------------ #
    # Formatting utilities
    # ------------------------------------------------------------------ #

    def _add_section_heading(self, doc: Document, text: str) -> None:
        p = doc.add_paragraph()
        p.space_before = Pt(14)
        p.space_after = Pt(4)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(12)
        run.font.color.rgb = NAVY
        self._add_bottom_border(doc, color=HEADER_BG, width=6)

    def _page_break(self, doc: Document) -> None:
        doc.add_page_break()

    @staticmethod
    def _shade_paragraph(paragraph: Any, fill_hex: str) -> None:
        pPr = paragraph._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill_hex)
        pPr.append(shd)

    @staticmethod
    def _shade_cell(cell: Any, fill_hex: str) -> None:
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill_hex)
        tcPr.append(shd)

    @staticmethod
    def _set_left_border(paragraph: Any, color: str = "1A3A6B", width: int = 12) -> None:
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
    def _add_bottom_border(doc: Document, color: str = "1A3A6B", width: int = 8) -> None:
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


__all__ = ["AdjudicationWordConverter"]
