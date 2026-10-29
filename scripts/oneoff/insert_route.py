"""Inject download_prohibition_order_docx route into routes.py."""

# The route's docstring is documented above (module docstring) and below
# (comment block); here we embed the route code itself as a raw string.

route = r"""@case_file_generator_bp.route("/case/<int:case_id>/docx/prohibition_order")
@login_required
def download_prohibition_order_docx(case_id: int):
    # Download the prohibition-order letter to the FBO manufacturer as Word (.docx).
    #
    # This is the SECOND document for an UNSAFE sample. It is generated only for
    # non-RCM cases (a separate manufacturer exists and batch/manufacturing/expiry
    # details are present). RCM (prepared/loose food) cases cannot have a
    # prohibition order issued — the endpoint returns 403 and invites the user to
    # download the Prayer instead. The letter repeats the first paragraph of the
    # Prayer (the sample description) so that it is self-contained.
    case = CaseFile.query.get_or_404(case_id)
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)

    # Unsafe_file.adoc / Prohibition_order.adoc template variables that differ
    # from the canonical CaseFile column names — alias them so the .adoc renders
    # from UI-entered data.
    case_data.setdefault("fso_name", case_data.get("food_safety_officer_name", ""))
    case_data.setdefault("sample_name", case_data.get("product_name", ""))

    # Per-packet sample quantity is derived (not a UI field): sample_quantity /
    # packet_count, formatted for the
    # "(per_packet X packet_count = sample_quantity)" equation in the letter.
    case_data["per_packet_sample_quantity"] = _compute_per_packet_sample_quantity(
        case_data.get("sample_quantity"), case_data.get("packet_count")
    )

    # A prohibition order is issued only to a separate manufacturer. Prepared/
    # loose food (retailer-cum-manufacturer) has no separate manufacturer and no
    # batch/manufacturing/expiry details, so no prohibition order can be issued.
    if case_data.get("retailer_cum_manufacturer"):
        return (
            jsonify(
                {
                    "error": "Prohibition order cannot be issued for prepared/loose "
                    "food (retailer-cum-manufacturer). The product has no separate "
                    "manufacturer and no batch/manufacturing/expiry details. "
                    "Download the Prayer to the Designated Officer instead."
                }
            ),
            403,
        )

    docx_bytes = render_docx("prohibition_order", case_data)

    buf = io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Prohibition_Order_{case.case_number or case_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
"""


if __name__ == "__main__":
    path = "app/case_file_generator/routes.py"
    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Insert right before the next route decorator (top-level marker).
    marker = '@case_file_generator_bp.route("/case/<int:case_id>/docx/zip")'
    assert marker in content, "marker not found"
    content = content.replace(marker, route + "\n\n" + marker)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print("route inserted")
