import io
import zipfile
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from app.extensions import db
from app.models import CaseFile
from app.utils.lookup import lookup_fssai
from app.utils.filters import format_date_indian
from app.services.sheets_sync import sync_to_sheets

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
    
    # Determine applicable FSS Act sections
    applicable_sections = get_applicable_sections(form_data)
    case_data['applicable_sections'] = applicable_sections
    case_data['applicable_sections_str'] = ' and '.join(applicable_sections)
        
    # Check if manufacturer and retailer are the same entity
    manufacturer_fssai = case_data.get('manufacturer_fssai', '').strip()
    retailer_fssai = case_data.get('retailer_fssai', '').strip()
    case_data['same_entity'] = (manufacturer_fssai == retailer_fssai)
    
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


@case_file_generator_bp.route('/')
def index():
    return render_template('case_file_generator/index.html')


@case_file_generator_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return jsonify({"error": error}), status_code
    return jsonify({"identity": result})


@case_file_generator_bp.route('/generate_case_file', methods=['POST'])
def generate_case_file_route():
    form_data = request.form.to_dict()
    
    # Save record to database
    case_file_record = CaseFile(
        case_number=form_data.get('case_number', ''),
        food_safety_officer_name=form_data.get('food_safety_officer_name', 'Suman Kumar Saha'),
        authorization_date=form_data.get('authorization_date', ''),
        inspection_date=form_data.get('inspection_date', ''),
        inspection_time=form_data.get('inspection_time', ''),
        
        manufacturer_fssai=form_data.get('manufacturer_fssai', ''),
        manufacturer_name=form_data.get('manufacturer_name', ''),
        manufacturer_fbo_name=form_data.get('manufacturer_fbo_name', ''),
        manufacturer_address=form_data.get('manufacturer_address', ''),
        
        retailer_fssai=form_data.get('retailer_fssai', ''),
        retailer_name=form_data.get('retailer_name', ''),
        retailer_fbo_name=form_data.get('retailer_fbo_name', ''),
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
        Lab_Registration_No=form_data.get('Lab_Registration_No', ''),
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
