"""
Case File Generator Module
Generates Petition and Permission Letter documents from form data
"""
import os
from jinja2 import Environment, FileSystemLoader
from num2words import num2words
from datetime import datetime

# Setup Jinja2 environment
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates', 'case_file_generator')
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

# Add custom filters
def to_words_filter(number):
    try:
        return num2words(int(number)).capitalize()
    except (ValueError, TypeError):
        return number

def format_date_indian(date_str):
    """Convert date string from DD/MM/YYYY to formatted date like '12th January 2026'"""
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        day = dt.day
        # Ordinal suffix logic
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix} {dt.strftime('%B')} {dt.year}"
    except Exception:
        return date_str

env.filters['to_words'] = to_words_filter
env.filters['format_date'] = format_date_indian


def process_form_data(form_data):
    """
    Process form data and prepare case_data dictionary for template rendering.
    
    Args:
        form_data: Dictionary of form data from the request
        
    Returns:
        Dictionary with all data needed for template rendering
    """
    # Convert date fields from YYYY-MM-DD to DD/MM/YYYY for formatting
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
        # Skip empty values
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
            
        # Convert date fields
        if key in date_fields and isinstance(value, str):
            # Convert from YYYY-MM-DD to DD/MM/YYYY
            try:
                dt = datetime.strptime(value, "%Y-%m-%d")
                case_data[key] = dt.strftime("%d/%m/%Y")
            except:
                case_data[key] = value
        else:
            case_data[key] = value
    
    # Handle checkbox fields (is_misbranded, is_substandard)
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
    
    # Format dates for display using the filter
    for field in date_fields:
        if field in case_data:
            # Apply the format_date filter to get formatted version
            case_data[field] = format_date_indian(case_data[field])
    
    # Check if manufacturer and retailer are the same entity
    manufacturer_fssai = case_data.get('manufacturer_fssai', '').strip()
    retailer_fssai = case_data.get('retailer_fssai', '').strip()
    case_data['same_entity'] = (manufacturer_fssai == retailer_fssai)
    
    # Format cost in words if not provided
    if 'cost_in_words' not in case_data or not case_data['cost_in_words']:
        total_cost = case_data.get('total_cost', '0')
        try:
            case_data['cost_in_words'] = to_words_filter(total_cost) + " Only"
        except:
            case_data['cost_in_words'] = ""
    
    return case_data


def generate_case_files(case_data, output_dir=None):
    """
    Generate Petition and Permission Letter documents.
    
    Args:
        case_data: Dictionary with all case data
        output_dir: Directory to save output files (default: current directory)
        
    Returns:
        Tuple of (petition_path, permission_letter_path) or (None, None) on error
    """
    if output_dir is None:
        output_dir = os.getcwd()
    
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # Load templates
        petition_template = env.get_template('petition.html')
        permission_template = env.get_template('permission_letter.html')
        
        # Render templates
        petition_output = petition_template.render(case_data)
        permission_output = permission_template.render(case_data)
        
        # Generate filenames
        case_number = case_data.get('case_number', 'unknown').replace('/', '_')
        petition_filename = f"Petition_{case_number}.doc"
        permission_filename = f"Permission_Letter_{case_number}.doc"
        
        petition_path = os.path.join(output_dir, petition_filename)
        permission_path = os.path.join(output_dir, permission_filename)
        
        # Save files
        with open(petition_path, "w", encoding="utf-8") as f:
            f.write(petition_output)
        
        with open(permission_path, "w", encoding="utf-8") as f:
            f.write(permission_output)
        
        return petition_path, permission_path
        
    except Exception as e:
        print(f"Error generating case files: {e}")
        return None, None


if __name__ == "__main__":
    # For testing - use sample data
    test_data = {
        "case_number": "2026/FSS/104",
        "manufacturer_name": "Sujeet Kr Banerjee",
        "manufacturer_fbo_name": "Jalpan Agro Foods Preparation",
        "manufacturer_address": "H. No. 88, Upper Basti, NH-33, Pardih Chowk, P.O Pardih, Mango, Purbi Singhbhun, Jharkhand-831012",
        "manufacturer_fssai": "11119006000057",
        "retailer_name": "Biyas Roy",
        "retailer_fbo_name": "M/S Arambagh Food mart Pvt. Ltd.",
        "retailer_address": "14/106 Uday Shankar Sarani, Ward No 94, Kolkata - 700033",
        "retailer_fssai": "12818019002830",
        "authorization_date": "2026-03-12",
        "inspection_date": "2025-11-28",
        "inspection_time": "12:40 PM",
        "other_food_articles": "Chanachur, Nimki, Roasted Chana",
        "product_name": "Taaja Jalpan Nilgiri Chanachur",
        "sample_quantity": "1000g",
        "packet_count": "4",
        "weight_per_packet": "250g",
        "batch_no": "IFF",
        "mfg_date": "2025-08-06",
        "expiry_date": "2025-12-05",
        "total_cost": "500",
        "cost_in_words": "Rupees Five Hundred Only",
        "sample_code": "SL/WB/110223/2025/13061",
        "sample_submission_date": "2025-11-28",
        "Lab_Registration_No": "WB/FOOD/2025/001",
        "do_receipt_date": "2025-12-19",
        "directive_letter_no": "H/FSSA/FSO/3054/2025-26",
        "directive_letter_date": "2025-12-19",
        "analyst_report_no": "PK/378/2025-26",
        "analyst_report_date": "2025-12-11",
        "retailer_report_receive_date": "2025-12-29",
        "manufacturer_report_receive_date": "2026-02-03",
        "applicable_regulation": "Regulation No 5(9)",
        "applicable_clause": "Clause (zf) of subsection 1 of section 3 of the FSSA,2006",
        "analysis_result": "misbranded",
        "food_safety_officer_name": "Suman Kumar Saha",
        "sample_name": "Taaja Jalpan Nilgiri Chanachur",
        "is_misbranded": "misbranded",
        "is_substandard": ""
    }
    
    case_data = process_form_data(test_data)
    generate_case_files(case_data)
    print("Test files generated successfully!")
