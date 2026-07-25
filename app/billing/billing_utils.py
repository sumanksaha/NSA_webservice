import io
import re
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from flask import current_app


def format_price(price_str):
    """
    Format a price string to a float, handling various formats.
    Returns float or 0.0 if parsing fails.
    """
    if not price_str:
        return 0.0
    
    try:
        # Strip non-numeric characters except decimal point
        cleaned = re.sub(r'[^\d.]', '', str(price_str))
        if not cleaned:
            return 0.0
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def compute_summary(samples):
    """
    Compute summary statistics from a list of sample records.
    
    Args:
        samples: List of Sample objects or dicts with keys: sample_type, price
        
    Returns:
        dict with:
        - by_type: dict {sample_type: {count, total_price}}
        - grand_total: float
        - total_count: int
    """
    by_type = {}
    total_count = 0
    grand_total = 0.0
    
    for sample in samples:
        if isinstance(sample, dict):
            sample_type = sample.get('sample_type')
            price_str = sample.get('price')
        else:
            sample_type = getattr(sample, 'sample_type', None)
            price_str = getattr(sample, 'price', None)
        
        # Normalize empty/None types to 'Other'
        if not sample_type:
            sample_type = 'Other'
        
        price = format_price(price_str)
        
        if sample_type not in by_type:
            by_type[sample_type] = {'count': 0, 'total_price': 0.0}
        
        by_type[sample_type]['count'] += 1
        by_type[sample_type]['total_price'] += price
        
        total_count += 1
        grand_total += price
    
    return {
        'by_type': by_type,
        'grand_total': grand_total,
        'total_count': total_count
    }


def generate_excel_report(samples, summary, start_date=None, end_date=None):
    """
    Generate an Excel workbook with two sheets: Samples and Summary.
    
    Args:
        samples: List of Sample objects (query results)
        summary: Summary dict from compute_summary()
        start_date: Optional start date string for filename
        end_date: Optional end date string for filename
    
    Returns:
        tuple: (Excel file bytes, filename)
    """
    wb = Workbook()
    
    # Remove default sheet
    if 'Sheet' in wb.sheetnames:
        del wb['Sheet']
    
    # === Sheet 1: Samples ===
    ws_samples = wb.create_sheet("Samples")
    
    # Header row
    headers = [
        "Sample Code",
        "Sample Name", 
        "Sample Type",
        "FSO Name",
        "Collection Date",
        "Submission Date",
        "Retailer FSSAI",
        "Retailer Name",
        "Price"
    ]
    
    for col_num, header in enumerate(headers, 1):
        cell = ws_samples.cell(row=1, column=col_num, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
        cell.border = Border(
            top=Side(style='thin'),
            bottom=Side(style='thin'),
            left=Side(style='thin'),
            right=Side(style='thin')
        )
        cell.fill = PatternFill(start_color='DDDDDD', end_color='DDDDDD', fill_type='solid')
    
    # Data rows
    for row_num, sample in enumerate(samples, 2):
        values = [
            getattr(sample, 'sample_code', ''),
            getattr(sample, 'sample_name', ''),
            getattr(sample, 'sample_type', '') or '',
            getattr(sample, 'fso_name', ''),
            getattr(sample, 'collection_date', ''),
            getattr(sample, 'submission_date', '') or '',
            getattr(sample, 'retailer_fssai', '') or '',
            getattr(sample, 'retailer_name', '') or '',
            getattr(sample, 'price', '') or ''
        ]
        
        for col_num, value in enumerate(values, 1):
            cell = ws_samples.cell(row=row_num, column=col_num, value=str(value) if value else '')
            cell.alignment = Alignment(horizontal='left')
            cell.border = Border(
                top=Side(style='thin'),
                bottom=Side(style='thin'),
                left=Side(style='thin'),
                right=Side(style='thin')
            )
    
    # Auto-adjust column widths for Samples sheet
    for col in ws_samples.columns:
        max_length = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws_samples.column_dimensions[col[0].column_letter].width = adjusted_width
    
    # === Sheet 2: Summary ===
    ws_summary = wb.create_sheet("Summary")
    
    # Header
    ws_summary.append([])  # Empty row
    ws_summary.append(["Billing Summary"])
    ws_summary.cell(row=2, column=1, value="Billing Summary").font = Font(bold=True, size=16)
    
    # Filter info
    ws_summary.append([])
    filter_info = []
    if start_date and end_date:
        filter_info = [f"Date Range: {start_date} to {end_date}"]
    elif start_date:
        filter_info = [f"From: {start_date}"]
    elif end_date:
        filter_info = [f"To: {end_date}"]
    else:
        filter_info = ["All Dates"]
    
    ws_summary.append(filter_info)
    ws_summary.append([])
    
    # Summary table headers
    summary_headers = ["Sample Type", "Count", "Total Price"]
    for col_num, header in enumerate(summary_headers, 1):
        cell = ws_summary.cell(row=6, column=col_num, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
        cell.border = Border(
            top=Side(style='thin'),
            bottom=Side(style='thin'),
            left=Side(style='thin'),
            right=Side(style='thin')
        )
        cell.fill = PatternFill(start_color='DDDDDD', end_color='DDDDDD', fill_type='solid')
    
    # Summary data rows
    by_type = summary.get('by_type', {})
    for row_num, (sample_type, data) in enumerate(by_type.items(), 7):
        count = data.get('count', 0)
        total_price = data.get('total_price', 0.0)
        
        values = [sample_type, count, total_price]
        for col_num, value in enumerate(values, 1):
            cell = ws_summary.cell(row=row_num, column=col_num, value=value)
            cell.alignment = Alignment(horizontal='left' if isinstance(value, str) else 'right')
            cell.border = Border(
                top=Side(style='thin'),
                bottom=Side(style='thin'),
                left=Side(style='thin'),
                right=Side(style='thin')
            )
            # Format price with 2 decimal places
            if col_num == 3 and isinstance(value, (int, float)):
                cell.number_format = '#,##0.00'
    
    # Grand total row
    grand_total_row = len(by_type) + 7
    ws_summary.append([])
    grand_total = summary.get('grand_total', 0.0)
    total_count = summary.get('total_count', 0)
    
    ws_summary.cell(row=grand_total_row, column=1, value="GRAND TOTAL").font = Font(bold=True)
    ws_summary.cell(row=grand_total_row, column=2, value=total_count).font = Font(bold=True)
    ws_summary.cell(row=grand_total_row, column=3, value=grand_total).font = Font(bold=True)
    
    # Format grand total
    for col_num in range(1, 4):
        cell = ws_summary.cell(row=grand_total_row, column=col_num)
        cell.alignment = Alignment(horizontal='right' if col_num > 1 else 'left')
        cell.border = Border(
            top=Side(style='thin'),
            bottom=Side(style='double'),
            left=Side(style='thin'),
            right=Side(style='thin')
        )
        if col_num == 3:
            cell.number_format = '#,##0.00'
    
    # Auto-adjust column widths for Summary sheet
    for col in ws_summary.columns:
        max_length = 0
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = (max_length + 2) * 1.2
        ws_summary.column_dimensions[col[0].column_letter].width = adjusted_width
    
    # === Save to BytesIO ===
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    
    # Generate filename
    if start_date and end_date:
        filename = f"billing_summary_{start_date}_to_{end_date}.xlsx"
    elif start_date:
        filename = f"billing_summary_{start_date}_to_end.xlsx"
    elif end_date:
        filename = f"billing_summary_start_to_{end_date}.xlsx"
    else:
        filename = "billing_summary_all.xlsx"
    
    # Clean filename (replace slashes with underscores)
    filename = filename.replace('/', '_').replace('\\', '_')
    
    return output, filename
