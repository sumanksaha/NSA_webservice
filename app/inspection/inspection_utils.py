"""
inspection_utils.py

Utilities for the Inspection module, including inspection_code generation.
"""

import threading
from datetime import datetime, timedelta
from app.extensions import db
from app.models import Inspection

# Thread lock for race-safe inspection code generation
_inspection_code_lock = threading.Lock()


def generate_inspection_code() -> str:
    """
    Generate an inspection code in the format INSP-YYYY-##### where ##### is zero-padded sequence per year.
    
    Uses a thread lock to ensure race-safe sequential writes for the same year.
    For a single-writer app, this guards against duplicate codes on rapid submits.
    
    Returns:
        str: Generated inspection code (e.g., 'INSP-2026-00001')
    """
    with _inspection_code_lock:
        year = datetime.utcnow().year
        
        # Get the maximum sequence number for this year
        from sqlalchemy import func
        
        # Inspection codes are in format INSP-YYYY-#####
        prefix = f"INSP-{year}-"
        
        # Query for max sequence number for this year
        max_code = db.session.query(func.max(Inspection.inspection_code)).filter(
            Inspection.inspection_code.like(f"{prefix}%")
        ).scalar()
        
        if max_code:
            # Extract the numeric part
            try:
                seq_num = int(max_code.split('-')[-1])
                next_seq = seq_num + 1
            except (ValueError, IndexError):
                # If there are malformed codes, get the count instead
                count = db.session.query(Inspection).filter(
                    Inspection.inspection_code.like(f"{prefix}%")
                ).count()
                next_seq = count + 1
        else:
            # No inspections for this year yet - start from 1
            next_seq = 1
        
        # Format with zero-padding to 5 digits
        inspection_code = f"INSP-{year}-{next_seq:05d}"
        
        return inspection_code


def calculate_compliance_deadline(inspection_date_str: str) -> str:
    """
    Calculate compliance deadline as inspection_date + 30 days.
    
    Args:
        inspection_date_str: Date string in ISO format (YYYY-MM-DD)
    
    Returns:
        str: Date string in ISO format for the deadline
    """
    try:
        inspection_date = datetime.strptime(inspection_date_str, '%Y-%m-%d')
        deadline = inspection_date + timedelta(days=30)
        return deadline.strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        # Return empty string if date is invalid
        return ''
