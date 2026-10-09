route = r"""@case_file_generator_bp.route("/case/<int:case_id>/docx/prohibition_order")
@login_required
def download_prohibition_order_docx(case_id: int):
    r'''Download the prohibition-order letter to the FBO manufacturer as Word (.docx).
    '''
    return None
"""

print("route defined:", route[:40])
