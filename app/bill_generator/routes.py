import io
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import Bill
from app.services.sheets_sync import sync_to_sheets

bill_generator_bp = Blueprint(
    'bill_generator',
    __name__,
    template_folder='templates',
    static_folder='static'
)

@bill_generator_bp.route('/')
def index():
    return render_template('bill_generator/index.html')


@bill_generator_bp.route('/generate_bill', methods=['POST'])
def generate_bill_route():
    form_data = request.form.to_dict()
    
    # Save record to database
    bill_record = Bill(
        Name=form_data.get('Name', ''),
        EMP_ID=form_data.get('EMP_ID', ''),
        Designation=form_data.get('Designation', 'Food Safety Officer'),
        Enf_samp_No=int(form_data.get('Enf_samp_No', 0)),
        Surv_samp_No=int(form_data.get('Surv_samp_No', 0)),
        Total_bill=form_data.get('Total_bill', ''),
        No_of_enfbills=form_data.get('No_of_enfbills', ''),
        No_of_survbills=form_data.get('No_of_survbills', ''),
        TR_Value=form_data.get('TR_Value', ''),
        TR_date=form_data.get('TR_date', ''),
        Submission_date=form_data.get('Submission_date', '')
    )
    
    db.session.add(bill_record)
    db.session.commit()
    
    # Try syncing to Google Sheets (new module-based sync)
    try:
        row_dict = {k: v for k, v in form_data.items() if k in bill_record.__dict__}
        row_dict['created_at'] = bill_record.created_at.isoformat() if bill_record.created_at else ""
        success = sync_to_sheets("billing", row_dict)
        if not success:
            current_app.logger.warning("Bill Generator: Sheets sync returned False - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Bill Generator: Sheets sync failed: {e}")
        
    # Render the PDF template
    rendered_html = render_template('bill_generator/template.html', **form_data)
    
    # Compile in-memory PDF via BytesIO
    pdf_buffer = io.BytesIO()
    try:
        from weasyprint import HTML

        HTML(string=rendered_html).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        filename = f"Inspection_Report_{bill_record.Name.replace(' ', '_')}.pdf"
        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        print(f"Error compiling billing report to PDF: {e}")
        return jsonify({"error": f"Failed to generate billing PDF: {str(e)}"}), 500
