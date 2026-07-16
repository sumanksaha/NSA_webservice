import io
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import Adjudication, FboIssue
from app.utils.lookup import lookup_ce, lookup_fssai
from app.utils.suggester import suggest_sections
from app.services.sheets_sync import sync_to_sheets
import json

adjudication_bp = Blueprint(
    'adjudication',
    __name__,
    template_folder='templates',
    static_folder='static'
)

CHECKLIST = [
    'clean_premise', 'refrigerator_clean', 'proper_attire',
    'proper_covered_utensil', 'date_tag', 'veg_nonveg_separation',
    'food_segregation', 'license_display', 'artificial_colour',
    'Expired_item', 'Pest_report', 'Water_report'
]

RULES = {
    'clean_premise': (
        "Unclean Premises",
        "The premises were found inadequately maintained and unhygienic."
    ),
    'refrigerator_clean': (
        "Improper Refrigerator Maintenance",
        "Refrigeration facilities were found unclean."
    ),
    'proper_attire': (
        "Improper Protective Attire",
        "Food handlers lacked prescribed attire."
    ),
    'proper_covered_utensil': (
        "Improper Covering of Food",
        "Food and utensils were uncovered."
    ),
    'date_tag': (
        "Absence of Date Tagging",
        "Stored food items lacked traceability."
    ),
    'veg_nonveg_separation': (
        "Improper Veg/Non-Veg Separation",
        "Segregation not maintained."
    ),
    'food_segregation': (
        "Improper Food Segregation",
        "Risk of cross contamination."
    ),
    'license_display': (
        "Improper License Display",
        "License not prominently displayed."
    ),
    'Expired_item': (
        "Expired Items",
        "Expired items present."
    ),
    'Pest_report': (
        "Pest Control Report Missing",
        "Routine pest control not documented."
    ),
    'Water_report': (
        "Water Test Report Missing",
        "Potable water testing unavailable."
    )
}


def adjudication_to_dict(adj):
    """
    Convert an Adjudication model instance to a dictionary for JSON serialization.
    This includes all fields needed for form pre-population and document regeneration.
    """
    return {
        'id': adj.id,
        'case_number': adj.case_number,
        'food_safety_officer': adj.food_safety_officer,
        'non_license': adj.non_license,
        'pre_authorization': adj.pre_authorization,
        'complaint_lodged': adj.complaint_lodged,
        'ce_license_no': adj.ce_license_no,
        'ce_trade_name': adj.ce_trade_name,
        'ce_proprietor': adj.ce_proprietor,
        'ce_address': adj.ce_address,
        'ce_status': adj.ce_status,
        'fbo_owner': adj.fbo_owner,
        'fbo_name': adj.fbo_name,
        'fbo_address': adj.fbo_address,
        'fssai_license': adj.fssai_license,
        'concerned_food': adj.concerned_food,
        'problem': adj.problem,
        'First_inspection_date': adj.First_inspection_date,
        'compliance_deadline': adj.compliance_deadline,
        'Complaint_date': adj.Complaint_date,
        'inspection_date': adj.inspection_date,
        'authorization_date': adj.authorization_date,
        'clean_premise': adj.clean_premise,
        'refrigerator_clean': adj.refrigerator_clean,
        'proper_attire': adj.proper_attire,
        'proper_covered_utensil': adj.proper_covered_utensil,
        'date_tag': adj.date_tag,
        'veg_nonveg_separation': adj.veg_nonveg_separation,
        'food_segregation': adj.food_segregation,
        'license_display': adj.license_display,
        'artificial_colour': adj.artificial_colour,
        'Expired_item': adj.Expired_item,
        'Pest_report': adj.Pest_report,
        'Water_report': adj.Water_report,
        'section_55': adj.section_55,
        'section_56': adj.section_56,
        'section_58': adj.section_58,
        'section_63': adj.section_63,
        'section_64': adj.section_64,
        'created_at': adj.created_at.isoformat() if adj.created_at else None,
        'synced_at': adj.synced_at.isoformat() if adj.synced_at else None
    }


@adjudication_bp.route('/')
def index():
    return render_template('adjudication/index.html', checklist=CHECKLIST)


@adjudication_bp.route('/lookup_ce', methods=['POST'])
def lookup_ce_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    if not license_no:
        return jsonify({"error": "License number is required."}), 400
    try:
        result = lookup_ce(license_no)
    except Exception as e:
        print(f"KMC portal request failed: {e}")
        return jsonify({"error": "Could not reach KMC portal. Try again."}), 502
    if not result:
        return jsonify({"error": "License not found."}), 404
    return jsonify(result)


@adjudication_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return jsonify({"error": error}), status_code
    return jsonify({"identity": result})


@adjudication_bp.route('/lookup_fbo_issues', methods=['GET'])
def lookup_fbo_issues():
    """
    Lookup FBO issues by fbo_id to provide pre-fill options for adjudication cases.
    Returns open and permission_granted issues that can be used to pre-fill adjudication forms.
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
        
        # Extract relevant pre-fill data for adjudication
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
        
        # Add source-specific prefill mappings for adjudication form fields
        if issue.source_type == 'inspection' and detail:
            prefill_data['prefill'] = {
                'fbo_name': issue.fbo_name,
                'fssai_license': issue.fbo_id,
                'fbo_owner': issue.fbo_name,  # Map to fbo_owner as default
                'concerned_food': detail.get('checklist', []),
                'problem': ', '.join(detail.get('checklist', [])),
                'inspection_date': detail.get('inspection_date'),
                'food_safety_officer': issue.fso_name
            }
        elif issue.source_type == 'sample' and detail:
            prefill_data['prefill'] = {
                'fbo_name': issue.fbo_name,
                'fssai_license': issue.fbo_id,
                'fbo_owner': issue.fbo_name,
                'concerned_food': detail.get('sample_name'),
                'problem': f"Sample issue: {detail.get('sample_name', '')} - {detail.get('sample_code', '')}",
                'sample_code': detail.get('sample_code'),
                'sample_name': detail.get('sample_name'),
                'sampling_date': detail.get('sampling_date'),
                'price': detail.get('price'),
                'food_safety_officer': issue.fso_name
            }
            # Add manufacturer info if present
            if issue.manufacturer_fbo_id:
                prefill_data['prefill']['manufacturer_fssai'] = issue.manufacturer_fbo_id
        else:
            prefill_data['prefill'] = {
                'fbo_name': issue.fbo_name,
                'fssai_license': issue.fbo_id,
                'fbo_owner': issue.fbo_name,
                'food_safety_officer': issue.fso_name
            }
        
        result.append(prefill_data)
    
    return jsonify(result), 200


@adjudication_bp.route('/suggest_sections', methods=['POST'])
def suggest_sections_route():
    # Accept standard form data
    form_data = request.form.to_dict()
    suggestions = suggest_sections(form_data)
    return jsonify(suggestions)


# Adjudication retrieval endpoints for data reuse
@adjudication_bp.route('/cases', methods=['GET'])
def list_adjudication_cases():
    """List all existing adjudication cases."""
    cases = Adjudication.query.order_by(Adjudication.created_at.desc()).all()
    return jsonify([{
        'id': c.id,
        'case_number': c.case_number,
        'fbo_name': c.fbo_name,
        'food_safety_officer': c.food_safety_officer,
        'created_at': c.created_at.isoformat() if c.created_at else None
    } for c in cases])


@adjudication_bp.route('/case/<int:case_id>', methods=['GET'])
def get_adjudication_case(case_id):
    """Retrieve a specific adjudication case by ID."""
    adj = Adjudication.query.get_or_404(case_id)
    return jsonify(adjudication_to_dict(adj))


@adjudication_bp.route('/case/by_number/<case_number>', methods=['GET'])
def get_adjudication_case_by_number(case_number):
    """Retrieve a specific adjudication case by case number."""
    adj = Adjudication.query.filter_by(case_number=case_number).first_or_404()
    return jsonify(adjudication_to_dict(adj))


@adjudication_bp.route('/regenerate/<int:case_id>', methods=['GET'])
def regenerate_adjudication_documents(case_id):
    """Regenerate documents from an existing adjudication case."""
    adj = Adjudication.query.get_or_404(case_id)
    form_data = adjudication_to_dict(adj)
    
    is_pre_authorization = str(form_data.get('pre_authorization', 'no')).strip().lower() == 'yes'
    
    # Render context
    context = form_data.copy()
    context['compilation_date'] = datetime.today().strftime("%d %B %Y")
    
    # Violations building
    violations = []
    for k, (title, obs) in RULES.items():
        if form_data.get(k) == 'no':
            violations.append({'title': title, 'Observation': obs})
            
    if form_data.get('artificial_colour') == 'yes':
        violations.append({
            'title': 'Use of Artificial Colours',
            'Observation': 'Artificial colours were reportedly used in food preparation.'
        })
        
    if form_data.get('Expired_item') == 'yes':
        violations.append({
            'title': 'Expired Items Present',
            'Observation': 'Expired food items were found on the premises.'
        })
        
    context['violations'] = violations
    
    outputs = []
    if is_pre_authorization:
        templates_to_generate = [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    else:
        if not form_data.get('authorization_date'):
            return jsonify({"error": "authorization_date is required for non-pre-authorization cases."}), 400
        templates_to_generate = [("adjudication/template_nonsample_petition.html", "Petition")]
        
    for tpl, prefix in templates_to_generate:
        from weasyprint import HTML
        rendered_html = render_template(tpl, **context)
        pdf_buffer = io.BytesIO()
        HTML(string=rendered_html).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        outputs.append((f"{prefix}.pdf", pdf_buffer.getvalue()))
        
    zip_prefix = "PermissionLetter" if is_pre_authorization else "Petition"
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for fname, data in outputs:
            z.writestr(fname, data)
            
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"{zip_prefix}_Case_{form_data.get('case_number', 'unknown')}_Regenerated.zip",
        mimetype="application/zip"
    )


@adjudication_bp.route('/generate_all', methods=['POST'])
def generate_all():
    form_data = request.form.to_dict()
    
    # Save record to local database
    adj = Adjudication(
        case_number=form_data.get('case_number', ''),
        food_safety_officer=form_data.get('food_safety_officer', ''),
        non_license=form_data.get('non_license', 'no'),
        pre_authorization=form_data.get('pre_authorization', 'no'),
        complaint_lodged=form_data.get('complaint_lodged', 'no'),
        
        ce_license_no=form_data.get('ce_license_no', ''),
        ce_trade_name=form_data.get('ce_trade_name', ''),
        ce_proprietor=form_data.get('ce_proprietor', ''),
        ce_address=form_data.get('ce_address', ''),
        ce_status=form_data.get('ce_status', ''),
        
        fbo_owner=form_data.get('fbo_owner', ''),
        fbo_name=form_data.get('fbo_name', ''),
        fbo_address=form_data.get('fbo_address', ''),
        fssai_license=form_data.get('fssai_license', ''),
        concerned_food=form_data.get('concerned_food', ''),
        problem=form_data.get('problem', ''),
        
        First_inspection_date=form_data.get('First_inspection_date', ''),
        compliance_deadline=form_data.get('compliance_deadline', ''),
        Complaint_date=form_data.get('Complaint_date', ''),
        inspection_date=form_data.get('inspection_date', ''),
        authorization_date=form_data.get('authorization_date', ''),
        
        # Checklist
        clean_premise=form_data.get('clean_premise', 'yes'),
        refrigerator_clean=form_data.get('refrigerator_clean', 'yes'),
        proper_attire=form_data.get('proper_attire', 'yes'),
        proper_covered_utensil=form_data.get('proper_covered_utensil', 'yes'),
        date_tag=form_data.get('date_tag', 'yes'),
        veg_nonveg_separation=form_data.get('veg_nonveg_separation', 'yes'),
        food_segregation=form_data.get('food_segregation', 'yes'),
        license_display=form_data.get('license_display', 'yes'),
        artificial_colour=form_data.get('artificial_colour', 'no'),
        Expired_item=form_data.get('Expired_item', 'no'),
        Pest_report=form_data.get('Pest_report', 'yes'),
        Water_report=form_data.get('Water_report', 'yes'),
        
        # Sections
        section_55=form_data.get('section_55', 'no'),
        section_56=form_data.get('section_56', 'no'),
        section_58=form_data.get('section_58', 'no'),
        section_63=form_data.get('section_63', 'no'),
        section_64=form_data.get('section_64', 'no'),
    )
    
    db.session.add(adj)
    db.session.commit()
    
    # Try syncing to Google Sheets (new module-based sync)
    try:
        row_dict = {k: v for k, v in form_data.items() if k in adj.__dict__}
        row_dict['created_at'] = adj.created_at.isoformat() if adj.created_at else ""
        success = sync_to_sheets("non_sample", row_dict)
        if not success:
            current_app.logger.warning("Adjudication: Sheets sync returned False - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Adjudication: Sheets sync failed: {e}")
        
    # Generate Adjudication Pack Documents in Memory
    is_pre_authorization = str(form_data.get('pre_authorization', 'no')).strip().lower() == 'yes'
    
    # Render context
    context = form_data.copy()
    context['compilation_date'] = datetime.today().strftime("%d %B %Y")
    
    # Violations building
    violations = []
    for k, (title, obs) in RULES.items():
        if form_data.get(k) == 'no':
            violations.append({
                'title': title,
                'Observation': obs
            })
            
    if form_data.get('artificial_colour') == 'yes':
        violations.append({
            'title': 'Use of Artificial Colours',
            'Observation': 'Artificial colours were reportedly used in food preparation.'
        })
        
    if form_data.get('Expired_item') == 'yes':
        violations.append({
            'title': 'Expired Items Present',
            'Observation': 'Expired food items were found on the premises.'
        })
        
    context['violations'] = violations
    
    outputs = []
    if is_pre_authorization:
        templates_to_generate = [
            ("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")
        ]
    else:
        if not form_data.get('authorization_date'):
            return "authorization_date is required when Pre-Authorization Case is not checked.", 400
        templates_to_generate = [
            ("adjudication/template_nonsample_petition.html", "Petition")
        ]
        
    for tpl, prefix in templates_to_generate:
        from weasyprint import HTML

        # Render the template to HTML string
        rendered_html = render_template(tpl, **context)
        
        # Compile HTML string to PDF using WeasyPrint in memory
        pdf_buffer = io.BytesIO()
        HTML(string=rendered_html).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        outputs.append((f"{prefix}.pdf", pdf_buffer.getvalue()))
        
    # Zip the outputs in memory
    zip_prefix = "PermissionLetter" if is_pre_authorization else "Petition"
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for fname, data in outputs:
            z.writestr(fname, data)
            
    zip_buffer.seek(0)
    
    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"{zip_prefix}_Final.zip",
        mimetype="application/zip"
    )
