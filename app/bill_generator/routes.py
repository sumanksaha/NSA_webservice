import io
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import Bill, FboIssue
from app.services.sheets_sync import sync_to_sheets
import json

bill_generator_bp = Blueprint(
    'bill_generator',
    __name__,
    template_folder='templates',
    static_folder='static'
)

@bill_generator_bp.route('/')
def index():
    return render_template('bill_generator/index.html')


@bill_generator_bp.route('/lookup_fbo_issues', methods=['GET'])
def lookup_fbo_issues():
    """
    Lookup FBO issues by fbo_id to provide pre-fill options for bill generation.
    Returns open and permission_granted issues that can be used to pre-fill bill forms.
    Query params: fbo_id (required), issue_id (optional - specific issue lookup)
    """
    fbo_id = request.args.get('fbo_id')
    issue_id = request.args.get('issue_id')
    
    if not fbo_id and not issue_id:
        return jsonify({'error': 'Either fbo_id or issue_id is required'}), 400
    
    query = FboIssue.query.filter(
        FboIssue.state.in_(['open', 'permission_granted'])
    )
    
    if issue_id:
        # Specific issue lookup by ID
        query = query.filter_by(id=int(issue_id))
    elif fbo_id:
        # Lookup all issues for this FBO
        query = query.filter_by(fbo_id=fbo_id)
    
    issues = query.order_by(FboIssue.created_at.desc()).all()
    
    result = []
    for issue in issues:
        # Parse detail_json
        detail = None
        if issue.detail_json:
            try:
                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json
        
        # Extract relevant pre-fill data for billing
        prefill_data = {
            'issue_id': issue.id,
            'fbo_id': issue.fbo_id,
            'manufacturer_fbo_id': issue.manufacturer_fbo_id,
            'fbo_name': issue.fbo_name,
            'source_type': issue.source_type,
            'state': issue.state,
            'fso_name': issue.fso_name,
            'created_at': issue.created_at,
            'detail': detail
        }
        
        # Add source-specific prefill mappings for bill form fields
        if issue.source_type == 'sample' and detail:
            # For sample issues, we can pre-fill sample-related billing fields
            prefill_data['prefill'] = {
                'Name': issue.fbo_name,  # FBO name as the primary name
                'EMP_ID': issue.fso_name,  # FSO name as default
                'Designation': 'Food Safety Officer',
                'sample_code': detail.get('sample_code'),
                'sample_name': detail.get('sample_name'),
                'price': detail.get('price'),
                'sampling_date': detail.get('sampling_date')
            }
            # If there's a manufacturer, they might be the bill recipient
            if issue.manufacturer_fbo_id:
                prefill_data['prefill']['manufacturer_fbo_id'] = issue.manufacturer_fbo_id
        elif issue.source_type == 'inspection' and detail:
            # For inspection issues, pre-fill with general info
            prefill_data['prefill'] = {
                'Name': issue.fbo_name,
                'EMP_ID': issue.fso_name,
                'Designation': 'Food Safety Officer',
                'inspection_details': ', '.join(detail.get('checklist', []))
            }
        else:
            # Generic pre-fill
            prefill_data['prefill'] = {
                'Name': issue.fbo_name,
                'EMP_ID': issue.fso_name,
                'Designation': 'Food Safety Officer'
            }
        
        result.append(prefill_data)
    
    return jsonify(result), 200


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
