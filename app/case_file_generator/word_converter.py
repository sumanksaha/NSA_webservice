#!/usr/bin/env python3
from docx import Document


class CaseFileWordConverter:
    def build_petition(self, ctx):
        doc = Document()
        doc.add_heading("Petition", 0)
        doc.add_paragraph(ctx.get("facts", ""))
        return doc

    def build_permission_letter(self, ctx):
        doc = Document()
        doc.add_heading("Permission Letter", 0)
        doc.add_paragraph(ctx.get("facts", ""))
        return doc
