"""Adoc→DOCX rendering bound to the case_file_generator template dir.

Thin wrapper over :mod:`app.shared.adoc_renderer` (ADR-001 pipeline), the
same pattern the non-sample adjudication blueprint uses.

The ``unsafe_file`` document additionally gets a section split: page 1 (the
14-column offender table) becomes a landscape section and everything after it
stays portrait. AsciiDoc roles cannot express that through pandoc, so the
section break is injected into the DOCX XML after rendering.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.shared.adoc_renderer import render_adoc_to_docx

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "case_file_generator"

# .adoc templates keyed by doc_type (source of truth per ADR-001)
ADOC_TEMPLATES = {
    "petition": "petition.adoc",
    "permission": "permission_letter.adoc",
    "unsafe_file": "Unsafe_file.adoc",
    "prohibition_order": "Prohibition_order.adoc",
}

# Letter portrait in twips — matches Word's and pandoc's effective default
# when a section carries no explicit w:pgSz.
_DEFAULT_PAGE_W = "12240"
_DEFAULT_PAGE_H = "15840"

# CT_SectPr elements that must follow w:pgSz (schema order) — used to insert
# a missing w:pgSz in the right position.
_AFTER_PG_SZ = (
    "w:pgMar",
    "w:paperSrc",
    "w:pgBorders",
    "w:lnNumType",
    "w:pgNumType",
    "w:cols",
    "w:formProt",
    "w:vAlign",
    "w:noEndnote",
    "w:titlePg",
    "w:textDirection",
    "w:bidi",
    "w:rtlGutter",
    "w:docGrid",
    "w:printerSettings",
)


def render_docx(doc_type: str, context: dict) -> bytes:
    """Render a case-file .adoc template to DOCX bytes.

    ``doc_type`` is ``petition``, ``permission`` or ``unsafe_file``.
    """
    template_name = ADOC_TEMPLATES[doc_type]
    docx_bytes = render_adoc_to_docx(TEMPLATE_DIR, template_name, context)
    if doc_type == "unsafe_file":
        docx_bytes = split_landscape_first_page(docx_bytes)
    return docx_bytes


def split_landscape_first_page(docx_bytes: bytes) -> bytes:
    """Make page 1 a landscape section; all following pages stay portrait.

    Inserts a ``nextPage`` section break directly after the document's first
    table (page 1 of ``Unsafe_file.adoc`` is table-only). The section that
    *ends* at the break paragraph carries a landscape ``w:pgSz``; the
    body-level ``w:sectPr`` is normalised to explicit portrait dimensions so
    the remaining sections render portrait regardless of consumer defaults.

    Returns the input unchanged when the document has no table or no section
    properties to copy from.
    """
    doc = Document(io.BytesIO(docx_bytes))
    body = doc.element.body

    tables = body.findall(qn("w:tbl"))
    final_sectpr = body.find(qn("w:sectPr"))
    if not tables or final_sectpr is None:
        return docx_bytes

    # Normalise the body section first so the deepcopy inherits explicit
    # portrait dimensions (pandoc often emits <w:sectPr> without w:pgSz at all).
    _ensure_portrait_size(final_sectpr)
    landscape_sectpr = _landscape_sectpr_from(final_sectpr)

    # Paragraph whose pPr holds the section properties that end section 1.
    break_para = OxmlElement("w:p")
    p_pr = OxmlElement("w:pPr")
    p_pr.append(landscape_sectpr)
    break_para.append(p_pr)
    tables[0].addnext(break_para)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _get_or_add_pg_sz(sectpr):
    """Return the ``w:pgSz`` child, inserting one if the section has none.

    Insertion respects the CT_SectPr sequence so Word does not reject the
    section properties: ``… footnotePr?, endnotePr?, type?, pgSz?, pgMar?, …``.
    """
    pg_sz = sectpr.find(qn("w:pgSz"))
    if pg_sz is not None:
        return pg_sz
    pg_sz = OxmlElement("w:pgSz")
    for tag in _AFTER_PG_SZ:
        anchor = sectpr.find(qn(tag))
        if anchor is not None:
            anchor.addprevious(pg_sz)
            return pg_sz
    sectpr.append(pg_sz)
    return pg_sz


def _ensure_portrait_size(sectpr) -> None:
    """Give ``sectpr`` explicit portrait Letter dimensions if it lacks them."""
    pg_sz = _get_or_add_pg_sz(sectpr)
    if not pg_sz.get(qn("w:w")) or not pg_sz.get(qn("w:h")):
        pg_sz.set(qn("w:w"), _DEFAULT_PAGE_W)
        pg_sz.set(qn("w:h"), _DEFAULT_PAGE_H)


def _landscape_sectpr_from(base_sectpr):
    """Deep-copy ``base_sectpr`` with a landscape page size and nextPage type.

    The copy is taken *after* ``_ensure_portrait_size`` has normalised the
    source, so the swap below always has explicit dimensions to work with.
    """
    sectpr = copy.deepcopy(base_sectpr)
    pg_sz = _get_or_add_pg_sz(sectpr)

    # Section type: explicitly nextPage (default is not guaranteed by consumers).
    type_el = sectpr.find(qn("w:type"))
    if type_el is None:
        type_el = OxmlElement("w:type")
        # w:type immediately precedes w:pgSz in the schema.
        pg_sz.addprevious(type_el)
    type_el.set(qn("w:val"), "nextPage")

    # Page size: portrait → landscape swap + w:orient.
    width = pg_sz.get(qn("w:w"))
    height = pg_sz.get(qn("w:h"))
    if not width or not height:
        width, height = _DEFAULT_PAGE_W, _DEFAULT_PAGE_H
    if int(width) < int(height):
        width, height = height, width
    pg_sz.set(qn("w:w"), width)
    pg_sz.set(qn("w:h"), height)
    pg_sz.set(qn("w:orient"), "landscape")

    return sectpr
