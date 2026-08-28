"""Word (.docx) converter for Case File documents (Petition + Permission Letter).

Builds professional government-style Word documents from the same context
data used by the HTML templates.  The output is suitable for email attachment
and official printing.

Usage::

    from app.case_file_generator.word_converter import CaseFileWordConverter

    converter = CaseFileWordConverter()
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


class CaseFileWordConverter:
    """Build .docx Case File documents from a Jinja2-style context dict.

    Expected keys (same as the HTML templates):
        case_number, food_safety_officer_name, authorization_date,
        inspection_date, inspection_time, manufacturer_name, manufacturer_fbo_name,
        manufacturer_address, manufacturer_fssai, retailer_name, retailer_fbo_name,
        retailer_address, retailer_fssai, product_name, batch_no, sample_quantity,
        packet_count, mfg_date, expiry_date, sample_code, lab_registration_no,
        do_receipt_date, analyst_report_no, analyst_report_date,
        directive_letter_no, directive_letter_date,
        retailer_report_receive_date, manufacturer_report_receive_date,
        analysis_result, applicable_sections, sections_display, same_entity,
        cost_in_words, total_cost, other_food_articles.
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
        font.name = "Arial"
        font.size = Pt(11)
        font.color.rgb = DARK_GRAY
        return doc

    # ------------------------------------------------------------------ #
    # Petition
    # ------------------------------------------------------------------ #

    def _add_petition_header(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        run = p.add_run("Before the Ld. Adjudicating Officer, KMC")
        run.bold = True
        run.font.size = Pt(11)

        p2 = doc.add_paragraph()
        run2 = p2.add_run(f"F.S. & S Case No. {ctx.get('case_number', '')}")
        run2.font.size = Pt(11)

        p3 = doc.add_paragraph()
        p3.space_before = Pt(6)
        r3 = p3.add_run("Complainant/Food Business Operator with Address\nCompany/Firm/Proprietor")
        r3.bold = True
        r3.font.size = Pt(11)

    def _add_parties(self, doc: Document, ctx: dict[str, Any]) -> None:
        """Two-column parties table."""
        table = doc.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        # Left column — Complainant
        cell_l = table.cell(0, 0)
        cell_l.width = Cm(8)
        p = cell_l.paragraphs[0]
        r = p.add_run(
            f"1. {ctx.get('food_safety_officer_name', '')}\nFood Safety Officer\nKolkata Municipal Corporation"
        )
        r.font.size = Pt(10)

        p2 = cell_l.add_paragraph()
        r2 = p2.add_run("2. License Officer\n5, S.N. Banerjee Road, Kolkata - 700013")
        r2.font.size = Pt(10)

        # Right column — Respondent(s)
        cell_r = table.cell(0, 1)
        cell_r.width = Cm(8)

        if ctx.get("same_entity"):
            p = cell_r.paragraphs[0]
            r = p.add_run(
                f"{ctx.get('retailer_name', '')}\n"
                f"Retailer-cum-Manufacturer\n"
                f"{ctx.get('retailer_fbo_name', '')}\n"
                f"{ctx.get('retailer_address', '')}\n"
                f"{ctx.get('retailer_fssai', '')}"
            )
            r.font.size = Pt(10)
        else:
            p = cell_r.paragraphs[0]
            r = p.add_run(
                f"{ctx.get('manufacturer_name', '')}\n"
                f"Manufacturer\n"
                f"{ctx.get('manufacturer_fbo_name', '')}\n"
                f"{ctx.get('manufacturer_address', '')}\n"
                f"{ctx.get('manufacturer_fssai', '')}"
            )
            r.font.size = Pt(10)

            p2 = cell_r.add_paragraph()
            p2.space_before = Pt(20)
            r2 = p2.add_run(
                f"{ctx.get('retailer_name', '')}\n"
                f"Retailer\n"
                f"{ctx.get('retailer_fbo_name', '')}\n"
                f"{ctx.get('retailer_address', '')}\n"
                f"{ctx.get('retailer_fssai', '')}"
            )
            r2.font.size = Pt(10)

        self._style_table_borders(table)

    def _add_petition_body(self, doc: Document, ctx: dict[str, Any]) -> None:
        # "Most Respectfully Sheweth"
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
            p.style = doc.styles["List Number"]
            p.clear()
            r_num = p.add_run(f"{i}. ")
            r_num.bold = True
            p.add_run(fact).font.size = Pt(11)

        # GROUNDS
        self._add_section_heading(doc, "GROUNDS")
        grounds = self._build_grounds(ctx)
        for i, ground in enumerate(grounds, 1):
            p = doc.add_paragraph()
            p.style = doc.styles["List Number"]
            p.clear()
            r_num = p.add_run(f"{i}. ")
            r_num.bold = True
            p.add_run(ground).font.size = Pt(11)

        # Legal notice box
        self._add_legal_notice_box(doc, ctx)

        # PRAYER
        self._add_section_heading(doc, "PRAYER")
        p = doc.add_paragraph()
        p.add_run(
            "In view of the facts and circumstances stated above, it is most "
            "respectfully prayed that this Hon'ble Adjudicating Officer may be "
            "pleased to:"
        )
        prayers = [
            "take cognizance of the complaint and initiate adjudication proceedings against the Food Business Operator(s) for the aforesaid contravention;",
            f"impose penalty upon the Food Business Operator(s) u/s {ctx.get('sections_display', '___')} of the Food Safety and Standards Act, 2006, as deemed fit and proper;",
            "award costs of the proceedings; and",
            "pass such other order or orders as may be deemed fit and proper in the interest of justice.",
        ]
        for prayer in prayers:
            p = doc.add_paragraph()
            p.style = doc.styles["List Number"]
            p.clear()
            p.add_run(prayer).font.size = Pt(11)

        p = doc.add_paragraph()
        p.space_before = Pt(8)
        p.add_run("And for this act of kindness your petitioner as in duty bound shall ever pray.")

        # Footer — Designated Officer + FSO
        self._add_signature_footer(doc, ctx)

    # ------------------------------------------------------------------ #
    # Permission Letter
    # ------------------------------------------------------------------ #

    def _add_permission_letter(self, doc: Document, ctx: dict[str, Any]) -> None:
        p = doc.add_paragraph()
        p.add_run("To,\nThe Designated Officer\nFood Cell, Kolkata Municipal Corporation")

        p2 = doc.add_paragraph()
        p2.space_before = Pt(12)
        r = p2.add_run("Sub: ")
        r.bold = True
        p2.add_run(
            f"Service of letter no. {ctx.get('directive_letter_no', '')} dated "
            f"{ctx.get('directive_letter_date', '')} along with Food Analyst's "
            f"report No. {ctx.get('analyst_report_no', '')} dated "
            f"{ctx.get('analyst_report_date', '')} in connection with the sample "
            f"of - {ctx.get('product_name', '')} (batch No - {ctx.get('batch_no', '')}, "
            f"Date of Manufacturing - {ctx.get('mfg_date', '')}, Date of expiry - "
            f"{ctx.get('expiry_date', '')}) sold by {ctx.get('retailer_fbo_name', '')} "
            f"({ctx.get('retailer_address', '')}, Lic No - {ctx.get('retailer_fssai', '')}) "
            f"collected on {ctx.get('inspection_date', '')} vide code No. "
            f"{ctx.get('sample_code', '')}"
        )
        sections = ctx.get("applicable_sections", [])
        if sections:
            p2.add_run(
                f" which was found to be {ctx.get('analysis_result', '')} and "
                f"contravenes Section 26(2)(ii) punishable under Section"
                f"{'s' if len(sections) > 1 else ''} "
                f"{ctx.get('sections_display', '')} of the FSS Act, 2006"
            )
        p2.add_run(".")

        p3 = doc.add_paragraph()
        p3.space_before = Pt(8)
        p3.add_run(
            f"In compliance to your order, an intimation letter vide no. "
            f"{ctx.get('directive_letter_no', '')} dated "
            f"{ctx.get('directive_letter_date', '')} along with Food Analyst's "
            f"report No. {ctx.get('analyst_report_no', '')} dated "
            f"{ctx.get('analyst_report_date', '')} declaring the sample of "
            f"{ctx.get('product_name', '')} (batch No - {ctx.get('batch_no', '')}, "
            f"Date of Manufacturing - {ctx.get('mfg_date', '')}, Date of expiry - "
            f"{ctx.get('expiry_date', '')}) manufactured by "
            f"{ctx.get('manufacturer_fbo_name', '')} "
            f"({ctx.get('manufacturer_address', '')}, Lic No - "
            f"{ctx.get('manufacturer_fssai', '')}) and sold by "
            f"{ctx.get('retailer_fbo_name', '')} "
            f"({ctx.get('retailer_address', '')}, Lic No - "
            f"{ctx.get('retailer_fssai', '')}) collected on "
            f"{ctx.get('inspection_date', '')} vide code No. "
            f"{ctx.get('sample_code', '')} as {ctx.get('analysis_result', '')}, "
            f"has been served upon to {ctx.get('retailer_name', '')} on "
            f"{ctx.get('retailer_report_receive_date', '')} and "
            f"{ctx.get('manufacturer_name', '')} on "
            f"{ctx.get('manufacturer_report_receive_date', '')}."
        )

        p4 = doc.add_paragraph()
        p4.add_run(
            "The Food Business Operator didn't appeal U/S 46(4) of Food Safety "
            "& Standard Act 2006 within the stipulated time as per FSSAI rules."
        )

        p5 = doc.add_paragraph()
        p5.add_run(
            f"Now, copies of all the relevant documents in connection with the "
            f"{ctx.get('product_name', '')} has been placed before you for further "
            f"necessary direction in this matter."
        )

        # Signature
        p6 = doc.add_paragraph()
        p6.space_before = Pt(30)
        r = p6.add_run(f"{ctx.get('food_safety_officer_name', '')}\n")
        r.bold = True
        p6.add_run("Food Safety Officer\nKolkata Municipal Corporation")

        # Enclosures
        p7 = doc.add_paragraph()
        p7.space_before = Pt(16)
        r = p7.add_run("List of Enclosures")
        r.bold = True

        enclosure_items = [
            f"Photocopies of Form V dated {ctx.get('inspection_date', '')}",
            f"Photocopies of Form VI dated {ctx.get('inspection_date', '')}",
            f"Lab submission receipt dated {ctx.get('do_receipt_date', '')}",
            f"Analyst Report {ctx.get('analyst_report_no', '')} dated {ctx.get('analyst_report_date', '')}",
            f"Directive {ctx.get('directive_letter_no', '')} dated {ctx.get('directive_letter_date', '')}",
            "Authorization of Designated Officer dated __________________",
        ]
        for i, item in enumerate(enclosure_items, 1):
            pi = doc.add_paragraph(style="List Number")
            pi.add_run(item)

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _build_facts(self, ctx: dict[str, Any]) -> list[str]:
        sections_display = ctx.get("sections_display", "___")
        same_entity = ctx.get("same_entity", False)

        facts = [
            (
                f"That the complainant above named is the Food Safety Officer "
                f'(hereinafter referred to as the "FSO") appointed u/s 37 of the Food '
                f"Safety and Standards Act, 2006 for whole area of Kolkata under Kolkata "
                f"Municipal Corporation and he has been authorised by the Designated "
                f"Officer, Kolkata Municipal Corporation dated {ctx.get('authorization_date', '')} "
                f"to launch the present complaint for adjudication before the Ld. "
                f"Adjudicating Officer, Kolkata Municipal Corporation."
            ),
            (
                f"That on {ctx.get('inspection_date', '')} at {ctx.get('inspection_time', '')}, "
                f"FSO inspected the premises of {ctx.get('retailer_fbo_name', '')} "
                f'(hereinafter referred to as the "retailer") situated at '
                f"{ctx.get('retailer_address', '')}, Lic No- {ctx.get('retailer_fssai', '')}, "
                f"where he found that said business was operated by "
                f"{ctx.get('retailer_name', '')}, who also disclosed that he was looking "
                f"after the day to day affairs of the retailer including storage and "
                f"selling of food articles."
            ),
            (
                f"That on inspection, it was found that different kinds of packaged food "
                f"articles such as {ctx.get('other_food_articles', '')} along with "
                f"{ctx.get('product_name', '')} stored there-in and exhibited for sale "
                f"for human consumption. Among the Food articles, one packet of "
                f"{ctx.get('product_name', '')} which appeared to FSO to be inferior in "
                f"quality and thus he intended to take sample for the purpose of analysis "
                f"by the Food Analyst."
            ),
            (
                f"That for the purpose of taking sample, and analyzing the said food "
                f"article by the Food Analyst, FSO called a local witness namely "
                f"Sandipan Sikder and in his presence the complainant served the notice "
                f"in form VA upon {ctx.get('retailer_name', '')} the above named which "
                f"was duly filled up and signed by the complainant and the witnesses."
            ),
            (
                f"That in presence of the Sample witnesses, the FSO / Complainant "
                f"purchased about {ctx.get('sample_quantity', '')} of "
                f"{ctx.get('product_name', '')} ({ctx.get('packet_count', '')} packets, "
                f"{ctx.get('batch_no', '')}, {ctx.get('mfg_date', '')}, "
                f'{ctx.get("expiry_date", "")}) (hereinafter referred to as the "sample"), '
                f"manufactured by {ctx.get('manufacturer_fbo_name', '')}, "
                f"{ctx.get('manufacturer_address', '')}, manufacturer Lic No - "
                f"{ctx.get('manufacturer_fssai', '')}, (herein referred to as the "
                f'"manufacturer"), from the food business premises of '
                f"{ctx.get('retailer_fbo_name', '')}, where it was kept and exhibited "
                f"for sale for human consumption and the retailer received a total amounting "
                f"to Rs. {ctx.get('total_cost', '')} ({ctx.get('cost_in_words', '')}) from "
                f"the complainant toward a sale proceed against which retailer issued a "
                f"valid cash receipt."
            ),
            (
                f"That the complainant sealed the samples and labeled in accordance with "
                f"the norms of FSSAI rules and no Formalin was added in accordance with "
                f"the method mentioned in Food Safety and Standards Act, 2006 and Rules "
                f"2011 & Regulations made there under on {ctx.get('inspection_date', '')}, "
                f"Then each packet was labelled with sample label coupon form which bears "
                f"the signature of the complainant, persons in charge of "
                f"{ctx.get('retailer_fbo_name', '')} the sample witnesses and code no. "
                f"of sample i.e {ctx.get('sample_code', '')}"
            ),
            (
                f"That each sample-packet with same and identical label was completely "
                f"wrapped with thick brown paper and the ends of the paper were neatly "
                f"folded in and were fixed by means of gum. A paper slip bearing signature "
                f"of Designated Officer with date and sample code no. "
                f"{ctx.get('sample_code', '')} which was issued by the Designated Officer, "
                f"Kolkata Municipal Corporation was pasted on the wrapper from bottom to "
                f"top on each sample-Packet. Then the signature of the person in charge was "
                f"taken on each part of the sample in such a manner that the paper slip and "
                f"the wrapper both carried a part of the signature of the person in charge. "
                f"Each packed sample-packet was further secured by strong red tape with knot "
                f"both above and across the sample-packet covered with brown paper. Then each "
                f"knot was covered by means of sealing wax on which distinct and clear "
                f"impression of the seal of Food Safety Officer was put on. One at the top, "
                f"one at the bottom and other two on the body of the packet following the "
                f"Rule 2.4.1 of the Food Safety & Standards Rules, 2011 in presence of "
                f"sample witness."
            ),
            (
                f"That one sealed packet containing one sample packet (Part I) of along with "
                f"a copy of memorandum duly filled in Form VI bearing code no. "
                f"{ctx.get('sample_code', '')} and specimen impression of the seal used by "
                f"the complainant Food Safety Officer was sent to the Food Analyst, Central "
                f"Laboratory (Food), K.M.C. under due entry in the peon book, which was duly "
                f"received by the Food Analyst, K.M.C. on {ctx.get('do_receipt_date', '')} "
                f"under Laboratory Registration No {ctx.get('lab_registration_no', '')}."
            ),
            (
                f"Two sealed packets — one containing two sample packets (Part II and Part III) "
                f"along with two copies of the memorandum duly filled in form VI bearing code "
                f"no. {ctx.get('sample_code', '')} and another containing one sample packet "
                f"(Part IV) along with one copy of the memorandum duly filled in Form VI "
                f"bearing code no. {ctx.get('sample_code', '')} were deposited at the Food "
                f"Cell under due entry in the peon book."
            ),
            (
                f"That in due course of time the complaint received the report of Food "
                f"Analyst bearing No: {ctx.get('analyst_report_no', '')} Dated "
                f"{ctx.get('analyst_report_date', '')} through the Designated Officer along "
                f"with a directive letter Vide No: {ctx.get('directive_letter_no', '')} "
                f"Dated {ctx.get('directive_letter_date', '')} which reveals that sampled "
                f"food article is"
                + (
                    f" The sample was found to be {ctx.get('analysis_result', '')}."
                    if ctx.get("analysis_result")
                    else ""
                )
            ),
            (
                f"That one copy of aforesaid Food Analyst report along with a forwarding "
                f"letter of Designated Officer vide No {ctx.get('directive_letter_no', '')} "
                f"Dated {ctx.get('directive_letter_date', '')} has been served upon "
                + (
                    f"{ctx.get('retailer_name', '')} on {ctx.get('retailer_report_receive_date', '')} and to {ctx.get('manufacturer_name', '')} on {ctx.get('manufacturer_report_receive_date', '')}"
                    if not same_entity
                    else f"{ctx.get('retailer_name', '')} on {ctx.get('retailer_report_receive_date', '')}"
                )
                + " by the complainant and the receipt of the same has been duly acknowledged. "
                "FSO also asked the manufacturer to prefer an appeal before Designated Officer "
                "against the finding of Food Analyst but the manufacturer did not prefer an "
                "appeal against the report of Food Analyst."
            ),
            (
                f"That the sample of {ctx.get('product_name', '')} is found to be in "
                f"contravention of Section 26(2)(ii) of the Food Safety and Standards Act, "
                f"2006 and thus the Food Business Operator(s) should be penalized "
                f"u/s {sections_display} of the Food Safety and Standards Act, 2006."
            ),
            (
                "That Food Safety Officer submitted all the relevant documents in connection "
                "with above sample and placed before the Designated Officer for his perusal "
                "and further proceedings."
            ),
            (
                "That the Designated officer authorized Food Safety Officer to file with "
                "the Adjudicating Officer an application for Adjudication."
            ),
        ]
        return facts

    def _build_grounds(self, ctx: dict[str, Any]) -> list[str]:
        sections_display = ctx.get("sections_display", "___")
        analysis_result = ctx.get("analysis_result", "")
        return [
            (
                f"That the sample of {ctx.get('product_name', '')} collected from the "
                f"premises of {ctx.get('retailer_fbo_name', '')} on "
                f"{ctx.get('inspection_date', '')} vide code No. "
                f"{ctx.get('sample_code', '')} was analysed by the Food Analyst, and the "
                f"report bearing No. {ctx.get('analyst_report_no', '')} dated "
                f"{ctx.get('analyst_report_date', '')} revealed that the sampled food article"
                + (
                    f" was found to be {analysis_result}."
                    if analysis_result
                    else " was not conforming to the standards prescribed under the Food Safety and Standards Act, 2006."
                )
            ),
            (
                f"That the said food article is in contravention of the provisions of "
                f"Section 26(2)(ii) of the Food Safety and Standards Act, 2006 and the "
                f"Food Business Operator(s) are liable to be penalised "
                f"u/s {sections_display} of the Food Safety and Standards Act, 2006."
            ),
            (
                "That the Food Business Operator(s) did not prefer any appeal against the "
                "report of the Food Analyst within the stipulated time as provided under "
                "Section 46(4) of the Food Safety and Standards Act, 2006."
            ),
            (
                "That the complainant has submitted all the relevant documents in connection "
                "with the above sample before the Designated Officer, who after perusal of "
                "the same has authorised the complainant to file the present petition for "
                "adjudication."
            ),
        ]

    def _add_legal_notice_box(self, doc: Document, ctx: dict[str, Any]) -> None:
        sections = ctx.get("applicable_sections", [])
        if not sections:
            return

        p = doc.add_paragraph()
        p.space_before = Pt(12)

        for sec in sections:
            p2 = doc.add_paragraph()
            r = p2.add_run(f"Liability under Section {sec}:")
            r.bold = True
            r.underline = True
            r.font.size = Pt(10)
            r.font.color.rgb = NAVY
            self._shade_paragraph(p2, LEGAL_BOX_BG)

            if sec == "51":
                text = (
                    "Any person who whether by himself or by any other person on his "
                    "behalf manufactures for sale or stores or sells or distributes or "
                    "imports any article of food for human consumption which is "
                    "sub-standard shall be liable to a penalty which may extend to "
                    "five lakh rupees."
                )
            elif sec == "52":
                text = (
                    "Any person who whether by himself or by any other person on his "
                    "behalf manufactures for sale or stores or sells or distributes or "
                    "imports any article of food for human consumption which is "
                    "misbranded shall be liable to a penalty which may extend to "
                    "three lakh rupees."
                )
            else:
                text = f"Liability provisions for Section {sec} apply."

            p3 = doc.add_paragraph()
            r3 = p3.add_run(text)
            r3.font.size = Pt(10)
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
        run.font.size = Pt(11)
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


__all__ = ["CaseFileWordConverter"]
