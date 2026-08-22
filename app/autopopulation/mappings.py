"""Field mappings: canonical OCR/Sample data → consumer form fields.

Source paths are dot-paths into the *verified record* built by
:func:`app.autopopulation.service.build_verified_record`:

    sample.<column>      — fields copied from the Sample row
    ocr.fields.<name>    — reviewed extraction payload fields
    lab.<param>.observed — observed value of a named lab-test parameter

Consumers declare which form field each source path feeds. A mapping that
resolves to ``None``/empty is simply omitted from the prefill bundle.
"""

MAPPINGS: dict[str, dict[str, str]] = {
    # Case-file petition form (Facts / Grounds / Prayer draw on these)
    "case_file": {
        "sample_code": "sample.sample_code",
        "sample_name": "sample.sample_name",
        "retailer_name": "sample.retailer_name",
        "retailer_fssai": "sample.retailer_fssai",
        "nature_of_food": "sample.nature_of_food",
        "batch_no": "sample.batch_no",
        "mfd": "sample.mfd",
        "exp": "sample.exp",
        "manufacturer_details": "sample.manufacturer_details",
        "document_title": "ocr.fields.title",
        "issuing_authority": "ocr.fields.authority",
        "document_date": "ocr.fields.date",
    },
    # Adjudication (non-sample) form shares the FBO/document identity fields
    "adjudication": {
        "manufacturer_details": "sample.manufacturer_details",
        "nature_of_food": "sample.nature_of_food",
        "document_title": "ocr.fields.title",
        "notification_number": "ocr.fields.notification_number",
        "document_date": "ocr.fields.date",
    },
    # Bill generator pre-fill
    "bill": {
        "sample_code": "sample.sample_code",
        "Enf_samp_No": "lab.__enf_count__",  # filled from lab params by the service
        "Surv_samp_No": "lab.__surv_count__",
    },
}


def resolve_path(record: dict, path: str):
    """Resolve a dotted source path (any depth) against a verified record."""
    node = record
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node
