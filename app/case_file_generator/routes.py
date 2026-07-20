import io
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import CaseFile, Sample
from app.utils.lookup import lookup_fssai
from app.utils.filters import format_date_indian
from app.services.sheets_sync import sync_to_sheets
from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS,
    DERIVED_SECTIONS_DISPLAY,
    DERIVED_CASE_TRACK,
    DERIVED_VIOLATIONS,
    DERIVED_SAME_ENTITY,
    SAMPLE_IS_SUBSTANDARD,
    SAMPLE_IS_MISBRANDED,
    PARTY_MANUFACTURER_FSSAI,
    PARTY_RETAILER_FSSAI,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_case_file,
    derive_sections_display,
    derive_same_entity,
)

case_file_generator_bp = Blueprint(
    'case_file_generator',
    __name__,
    template_folder='templates',
    static_folder='static'
)

def get_applicable_sections(form_data: dict) -> list:
    """
    Determine applicable FSS Act sections based on analysis result.
    
    Rules:
    - Substandard sample -> Section 51
    - Misbranded sample -> Section 52
    - Both -> Sections 51 and 52
    
    Returns:
        list: Sorted list of section numbers as strings (e.g., ['51'], ['52'], ['51', '52'])
    """
    sections = []
    is_misbranded = form_data.get('is_misbranded') == 'misbranded'
    is_substandard = form_data.get('is_substandard') == 'substandard'
    
    if is_substandard:
        sections.append('51')
    if is_misbranded:
        sections.append('52')
    
    return sorted(sections)


def process_form_data(form_data):
    """
    Process form data and prepare case_data dictionary for template rendering and model saving.
    """
    date_fields = [
        'authorization_date',
        'inspection_date', 
        'mfg_date',
        'expiry_date',
        'sample_submission_date',
        'do_receipt_date',
        'analyst_report_date',
        'directive_letter_date',
        'retailer_report_receive_date',
        'manufacturer_report_receive_date'
    ]
    
    case_data = {}
    
    # Copy all form data
    for key, value in form_data.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
            
        # Parse from YYYY-MM-DD input date fields
        if key in date_fields and isinstance(value, str):
            try:
                dt = datetime.strptime(value, "%Y-%m-%d")
                case_data[key] = dt.strftime("%d/%m/%Y")
            except:
                case_data[key] = value
        else:
            case_data[key] = value
            
    # Handle checkbox fields
    is_misbranded = form_data.get('is_misbranded') == 'misbranded'
    is_substandard = form_data.get('is_substandard') == 'substandard'
    
    case_data['is_misbranded'] = is_misbranded
    case_data['is_substandard'] = is_substandard
    
    # Determine analysis_result string
    if is_misbranded and is_substandard:
        case_data['analysis_result'] = "misbranded and substandard"
    elif is_misbranded:
        case_data['analysis_result'] = "misbranded"
    elif is_substandard:
        case_data['analysis_result'] = "substandard"
    else:
        case_data['analysis_result'] = ""
    
    # STEP 4: Derive applicable sections using shared helper
    applicable_sections = derive_applicable_sections_from_case_file(
        is_substandard=is_substandard,
        is_misbranded=is_misbranded,
    )
    
    # Keep backward compatible field for templates
    case_data['applicable_sections'] = applicable_sections
    case_data['applicable_sections_str'] = ' and '.join(applicable_sections)
    
    # STEP 4: Add canonical derived context fields
    case_data[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    case_data[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    case_data[DERIVED_CASE_TRACK] = 'sample'  # Case file is always sample track
    case_data[DERIVED_VIOLATIONS] = []  # Sample cases don't have violations
    
    # STEP 4: Derive same_entity using shared helper
    # Get FSSAI values from case_data (already processed) or form_data
    manufacturer_fssai = case_data.get('manufacturer_fssai_license', case_data.get('manufacturer_fssai', '')).strip()
    retailer_fssai = case_data.get('retailer_fssai_license', case_data.get('retailer_fssai', '')).strip()
    same_entity = derive_same_entity(manufacturer_fssai, retailer_fssai)
    case_data['same_entity'] = same_entity
    case_data[DERIVED_SAME_ENTITY] = same_entity
    
    # Pre-format dates for display
    for field in date_fields:
        if field in case_data:
            case_data[field] = format_date_indian(case_data[field])
            
    # Format cost in words if not provided
    if 'cost_in_words' not in case_data or not case_data['cost_in_words']:
        total_cost = case_data.get('total_cost', '0')
        try:
            from app.utils.filters import to_words
            case_data['cost_in_words'] = to_words(total_cost) + " Only"
        except:
            case_data['cost_in_words'] = ""
            
    return case_data


def case_file_to_dict(case_file):
    """
    Convert a CaseFile model instance to a dictionary for JSON serialization.
    This includes all fields needed for form pre-population and document regeneration.
    Map DB columns to canonical keys for Step 3.
    """
    return {
        'id': case_file.id,
        'case_number': case_file.case_number,
        'food_safety_officer_name': case_file.food_safety_officer_name,
        'authorization_date': case_file.authorization_date,
        'sample_draw_date': case_file.inspection_date,  # DB column: inspection_date -> canonical
        'sample_draw_time': case_file.inspection_time,  # DB column: inspection_time -> canonical
        'sample_id': case_file.sample_id,  # Step 5: Link to Sample
        'manufacturer_fssai_license': case_file.manufacturer_fssai,  # DB column -> canonical
        'manufacturer_person_name': case_file.manufacturer_name,  # DB column -> canonical
        'manufacturer_trade_name': case_file.manufacturer_fbo_name,  # DB column -> canonical
        'manufacturer_address': case_file.manufacturer_address,
        'retailer_fssai_license': case_file.retailer_fssai,  # DB column -> canonical
        'retailer_person_name': case_file.retailer_name,  # DB column -> canonical
        'retailer_trade_name': case_file.retailer_fbo_name,  # DB column -> canonical
        'retailer_address': case_file.retailer_address,
        'product_name': case_file.product_name,
        'batch_no': case_file.batch_no,
        'sample_quantity': case_file.sample_quantity,
        'packet_count': case_file.packet_count,
        'mfg_date': case_file.mfg_date,
        'expiry_date': case_file.expiry_date,
        'other_food_articles': case_file.other_food_articles,
        'total_cost': case_file.total_cost,
        'cost_in_words': case_file.cost_in_words,
        'sample_code': case_file.sample_code,
        'sample_submission_date': case_file.sample_submission_date,
        'lab_registration_no': case_file.Lab_Registration_No,  # DB column -> canonical
        'do_receipt_date': case_file.do_receipt_date,
        'is_misbranded': 'misbranded' if case_file.is_misbranded else '',
        'is_substandard': 'substandard' if case_file.is_substandard else '',
        'analyst_report_no': case_file.analyst_report_no,
        'analyst_report_date': case_file.analyst_report_date,
        'directive_letter_no': case_file.directive_letter_no,
        'directive_letter_date': case_file.directive_letter_date,
        'retailer_report_receive_date': case_file.retailer_report_receive_date,
        'manufacturer_report_receive_date': case_file.manufacturer_report_receive_date,
        'applicable_regulation': case_file.applicable_regulation,
        'applicable_clause': case_file.applicable_clause,
        'sample_name': case_file.sample_name,
        'applicable_sections': case_file.applicable_sections,
        'created_at': case_file.created_at.isoformat() if case_file.created_at else None,
        'synced_at': case_file.synced_at.isoformat() if case_file.synced_at else None
    }


@case_file_generator_bp.route('/')
def index():
    return render_template('case_file_generator/index.html')


# Case retrieval endpoints for data reuse
@case_file_generator_bp.route('/cases', methods=['GET'])
def list_cases():
    """List all existing case files."""
    cases = CaseFile.query.order_by(CaseFile.created_at.desc()).all()
    return jsonify([{
        'id': c.id,
        'case_number': c.case_number,
        'product_name': c.product_name,
        'manufacturer_name': c.manufacturer_name,
        'created_at': c.created_at.isoformat() if c.created_at else None
    } for c in cases])


@case_file_generator_bp.route('/case/<int:case_id>', methods=['GET'])
def get_case(case_id):
    """Retrieve a specific case file by ID."""
    case_file = CaseFile.query.get_or_404(case_id)
    return jsonify(case_file_to_dict(case_file))


@case_file_generator_bp.route('/case/by_number/<case_number>', methods=['GET'])
def get_case_by_number(case_number):
    """Retrieve a specific case file by case number."""
    case_file = CaseFile.query.filter_by(case_number=case_number).first_or_404()
    return jsonify(case_file_to_dict(case_file))


@case_file_generator_bp.route('/regenerate/<int:case_id>', methods=['GET'])
def regenerate_case_files(case_id):
    """Regenerate both Petition and Permission Letter from an existing case."""
    case_file = CaseFile.query.get_or_404(case_id)
    form_data = case_file_to_dict(case_file)
    case_data = process_form_data(form_data)
    
    try:
        from weasyprint import HTML
        petition_html = render_template('case_file_generator/petition.html', **case_data)
        permission_html = render_template('case_file_generator/permission_letter.html', **case_data)
        
        petition_pdf = io.BytesIO()
        HTML(string=petition_html).write_pdf(petition_pdf)
        petition_pdf.seek(0)
        
        permission_pdf = io.BytesIO()
        HTML(string=permission_html).write_pdf(permission_pdf)
        permission_pdf.seek(0)
        
        zip_buffer = io.BytesIO()
        case_number = case_data.get('case_number', 'unknown').replace('/', '_')
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"Petition_{case_number}.pdf", petition_pdf.getvalue())
            zf.writestr(f"Permission_Letter_{case_number}.pdf", permission_pdf.getvalue())
        
        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"Case_Files_{case_number}_Regenerated.zip",
            mimetype="application/zip"
        )
    except Exception as e:
        return jsonify({"error": f"Failed to regenerate: {str(e)}"}), 500


@case_file_generator_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return jsonify({"error": error}), status_code
    return jsonify({"identity": result})


@case_file_generator_bp.route('/lookup_sample', methods=['GET'])
def lookup_sample():
    """Lookup sample by sample_code for CaseFile prefill."""
    sample_code = request.args.get('sample_code', '').strip()
    if not sample_code:
        return jsonify({'error': 'sample_code is required'}), 400
    
    sample = Sample.query.filter_by(sample_code=sample_code).first()
    if not sample:
        return jsonify({'error': f'Sample with code {sample_code} not found'}), 404
    
    # Return sample data for prefill - using canonical keys for Step 3
    return jsonify({
        'id': sample.id,
        'sample_code': sample.sample_code,
        'sample_name': sample.sample_name,
        'retailer_fssai_license': sample.retailer_fssai or '',  # canonical
        'retailer_person_name': sample.retailer_name or '',  # canonical
        'sample_submission_date': sample.submission_date or '',  # canonical
        'total_cost': sample.price or ''  # canonical (DB column: price)
    })


@case_file_generator_bp.route('/samples', methods=['GET'])
def list_samples_for_datalist():
    """List all samples for datalist dropdown (returns sample codes only)."""
    samples = Sample.query.order_by(Sample.sample_code.desc()).all()
    sample_codes = [sample.sample_code for sample in samples]
    return jsonify({'sample_codes': sample_codes})


@case_file_generator_bp.route('/generate_case_file', methods=['POST'])
def generate_case_file_route():
    form_data = request.form.to_dict()
    
    # Handle sample_id linkage (Step 5)
    sample_id = None
    if 'sample_id' in form_data and form_data['sample_id']:
        try:
            sample_id = int(form_data['sample_id'])
        except ValueError:
            # If sample_id is not a valid integer, ignore it
            pass
    
    # Save record to database - using canonical keys from Step 2
    case_file_record = CaseFile(
        case_number=form_data.get('case_number', ''),
        food_safety_officer_name=form_data.get('food_safety_officer_name', ''),
        authorization_date=form_data.get('authorization_date', ''),
        inspection_date=form_data.get('sample_draw_date', ''),  # canonical: sample_draw_date -> DB column inspection_date
        inspection_time=form_data.get('sample_draw_time', ''),  # canonical
        sample_id=sample_id,  # Step 5: Link to Sample
        
        manufacturer_fssai=form_data.get('manufacturer_fssai_license', ''),  # canonical
        manufacturer_name=form_data.get('manufacturer_person_name', ''),  # canonical
        manufacturer_fbo_name=form_data.get('manufacturer_trade_name', ''),  # canonical
        manufacturer_address=form_data.get('manufacturer_address', ''),
        
        retailer_fssai=form_data.get('retailer_fssai_license', ''),  # canonical
        retailer_name=form_data.get('retailer_person_name', ''),  # canonical
        retailer_fbo_name=form_data.get('retailer_trade_name', ''),  # canonical
        retailer_address=form_data.get('retailer_address', ''),
        
        product_name=form_data.get('product_name', ''),
        batch_no=form_data.get('batch_no', ''),
        sample_quantity=form_data.get('sample_quantity', ''),
        packet_count=int(form_data.get('packet_count', 4)),
        mfg_date=form_data.get('mfg_date', ''),
        expiry_date=form_data.get('expiry_date', ''),
        other_food_articles=form_data.get('other_food_articles', ''),
        total_cost=form_data.get('total_cost', ''),
        cost_in_words=form_data.get('cost_in_words', ''),
        
        sample_code=form_data.get('sample_code', ''),
        sample_submission_date=form_data.get('sample_submission_date', ''),
        Lab_Registration_No=form_data.get('lab_registration_no', ''),  # canonical
        do_receipt_date=form_data.get('do_receipt_date', ''),
        
        is_misbranded=form_data.get('is_misbranded') == 'misbranded',
        is_substandard=form_data.get('is_substandard') == 'substandard',
        analyst_report_no=form_data.get('analyst_report_no', ''),
        analyst_report_date=form_data.get('analyst_report_date', ''),
        directive_letter_no=form_data.get('directive_letter_no', ''),
        directive_letter_date=form_data.get('directive_letter_date', ''),
        retailer_report_receive_date=form_data.get('retailer_report_receive_date', ''),
        manufacturer_report_receive_date=form_data.get('manufacturer_report_receive_date', ''),
        
        applicable_regulation=form_data.get('applicable_regulation', ''),
        applicable_clause=form_data.get('applicable_clause', ''),
        sample_name=form_data.get('sample_name', ''),
        applicable_sections=', '.join(get_applicable_sections(form_data))
    )
    
    db.session.add(case_file_record)
    db.session.commit()
    
    # Try syncing to Google Sheets (new module-based sync)
    try:
        row_dict = {k: v for k, v in form_data.items() if k in case_file_record.__dict__}
        row_dict['created_at'] = case_file_record.created_at.isoformat() if case_file_record.created_at else ""
        row_dict['applicable_sections'] = case_file_record.applicable_sections
        row_dict['sample_id'] = case_file_record.sample_id  # Step 5: Include sample_id in sync
        success = sync_to_sheets("sample", row_dict)
        if not success:
            current_app.logger.warning("Case File: Sheets sync returned False - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Case File: Sheets sync failed: {e}")
        
    # Render templates and compile in-memory PDFs using WeasyPrint
    case_data = process_form_data(form_data)
    
    try:
        from weasyprint import HTML

        petition_html = render_template('case_file_generator/petition.html', **case_data)
        permission_html = render_template('case_file_generator/permission_letter.html', **case_data)
        
        # Write to in-memory buffers
        petition_pdf = io.BytesIO()
        HTML(string=petition_html).write_pdf(petition_pdf)
        petition_pdf.seek(0)
        
        permission_pdf = io.BytesIO()
        HTML(string=permission_html).write_pdf(permission_pdf)
        permission_pdf.seek(0)
        
        # Package into in-memory ZIP file
        zip_buffer = io.BytesIO()
        case_number = case_data.get('case_number', 'unknown').replace('/', '_')
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"Petition_{case_number}.pdf", petition_pdf.getvalue())
            zf.writestr(f"Permission_Letter_{case_number}.pdf", permission_pdf.getvalue())
            
        zip_buffer.seek(0)
        
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"Case_Files_{case_number}.zip",
            mimetype="application/zip"
        )
        
    except Exception as e:
        print(f"Error generating case files in memory: {e}")
        return jsonify({"error": f"Failed to generate case files: {str(e)}"}), 500
