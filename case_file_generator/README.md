# Case File Generator

This module generates Petition and Permission Letter documents for FSSAI food safety cases.

## Structure

```
case_file_generator/
├── __init__.py
├── case_file_generator.py  # Main module with form processing and file generation
└── templates/
    └── case_file_generator/
        ├── petition.html          # Petition document template
        └── permission_letter.html  # Permission letter template
```

## Features

- **Web Form**: HTML form for entering case details
- **FSSAI Lookup**: Automatic lookup of manufacturer and retailer details from FSSAI databases
- **Template Rendering**: Generates formatted DOC files using Jinja2 templates
- **Date Formatting**: Converts dates to proper format (e.g., "12th January 2026")
- **Checkbox Handling**: Supports misbranded and substandard analysis results

## Dependencies

- FastAPI
- Jinja2
- num2words
- SQLite3 (for FSSAI database lookups)

## Usage

### As a Web Application

The module integrates with the main `app.py` to provide:

1. **Form Route**: `GET /case_file_form` - Renders the case file generation form
2. **Generation Route**: `POST /generate_case_file` - Processes form data and generates documents
3. **FSSAI Lookup**: `POST /lookup_fssai` - (Already exists in app.py) - Fetches FBO details from databases

### As a Standalone Module

```python
from case_file_generator.case_file_generator import process_form_data, generate_case_files

# Prepare form data
form_data = {
    "case_number": "2026/FSS/104",
    "manufacturer_fssai": "11119006000057",
    "retailer_fssai": "12818019002830",
    "product_name": "Taaja Jalpan Nilgiri Chanachur",
    # ... other fields
    "is_misbranded": "misbranded",
    "is_substandard": ""
}

# Process and generate
case_data = process_form_data(form_data)
petition_path, permission_path = generate_case_files(case_data, output_dir="output")
```

## Form Fields

### Case Information
- `case_number`: Case reference number (e.g., "2026/FSS/104")
- `food_safety_officer_name`: Name of the FSO (default: "Suman Kumar Saha")
- `authorization_date`: Date of authorization (YYYY-MM-DD)
- `inspection_date`: Date of inspection (YYYY-MM-DD)
- `inspection_time`: Time of inspection (HH:MM)

### Manufacturer Information
- `manufacturer_fssai`: FSSAI number (starts with 1 or 2) - **Lookup enabled**
- `manufacturer_name`: Auto-filled from FSSAI lookup
- `manufacturer_fbo_name`: Auto-filled from FSSAI lookup
- `manufacturer_address`: Auto-filled from FSSAI lookup

### Retailer Information
- `retailer_fssai`: FSSAI number (starts with 1 or 2) - **Lookup enabled**
- `retailer_name`: Auto-filled from FSSAI lookup
- `retailer_fbo_name`: Auto-filled from FSSAI lookup
- `retailer_address`: Auto-filled from FSSAI lookup

### Product Information
- `product_name`: Name of the product
- `batch_no`: Batch number
- `sample_quantity`: Sample quantity (e.g., "1000g")
- `packet_count`: Number of packets
- `mfg_date`: Manufacturing date (YYYY-MM-DD)
- `expiry_date`: Expiry date (YYYY-MM-DD)
- `other_food_articles`: Other food items found
- `total_cost`: Total cost in Rs.
- `cost_in_words`: Cost in words (can be auto-generated)

### Sample Information
- `sample_code`: Sample code (e.g., "SL/WB/110223/2025/13061")
- `sample_submission_date`: Date sample was submitted (YYYY-MM-DD)
- `Lab_Registration_No`: Lab registration number
- `do_receipt_date`: DO receipt date (YYYY-MM-DD)

### Analysis Result
- `is_misbranded`: Checkbox - if checked, value is "misbranded"
- `is_substandard`: Checkbox - if checked, value is "substandard"
- `analyst_report_no`: Analyst report number
- `analyst_report_date`: Analyst report date (YYYY-MM-DD)
- `directive_letter_no`: Directive letter number
- `directive_letter_date`: Directive letter date (YYYY-MM-DD)
- `retailer_report_receive_date`: Date retailer received report (YYYY-MM-DD)
- `manufacturer_report_receive_date`: Date manufacturer received report (YYYY-MM-DD)
- `applicable_regulation`: Regulation number
- `applicable_clause`: Applicable clause
- `sample_name`: Sample name

## FSSAI Database Lookup

The system automatically fetches details from the FSSAI databases:

- **License Database** (`db/license_data.db`): For FSSAI numbers starting with '1'
  - Table: `license_records`
  - Fields: `license_no`, `company_name`, `full_address`, `expiry_date`

- **Registration Database** (`db/registration_data.db`): For FSSAI numbers starting with '2'
  - Table: `registration_records`
  - Fields: `registration_no`, `company_name`, `full_address`, `expiry_date`

## Output

The system generates two DOC files:
1. `Petition_{case_number}.doc` - The petition document
2. `Permission_Letter_{case_number}.doc` - The permission letter

These are packaged into a ZIP file: `Case_Files_{case_number}.zip`

## Template Variables

All variables used in the templates must be provided in the `case_data` dictionary. The templates support:

- Date formatting using the `format_date` filter (converts DD/MM/YYYY to "12th January 2026")
- Number to words conversion using the `to_words` filter
- Conditional rendering based on `is_misbranded`, `is_substandard`, and `same_entity` flags

## Testing

Run the test script:

```bash
python case_file_generator.py
```

This will generate test files using sample data.

## Integration with FastAPI

The module is automatically integrated with the main `app.py` application. Add a link to `/case_file_form` in your navigation to access the form.
