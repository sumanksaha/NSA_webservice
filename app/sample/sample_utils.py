"""
sample_utils.py

Utilities for the Sample module, including sample_code generation.
"""

import threading
from datetime import datetime
from app.extensions import db
from app.models import Sample

# Thread lock for race-safe sample code generation
_sample_code_lock = threading.Lock()


def generate_sample_code() -> str:
    """
    Generate a sample code in the format SKS-YYYY-##### where ##### is zero-padded sequence per year.
    
    Uses a thread lock to ensure race-safe sequential writes for the same year.
    For a single-writer app, this guards against duplicate codes on rapid submits.
    
    Returns:
        str: Generated sample code (e.g., 'SKS-2026-00001')
    """
    with _sample_code_lock:
        year = datetime.utcnow().year
        
        # Get the maximum sequence number for this year
        from sqlalchemy import func
        
        # Extract the numeric part from existing codes for this year
        # Sample codes are in format SKS-YYYY-#####
        prefix = f"SKS-{year}-"
        
        # Query for max sequence number for this year
        max_code = db.session.query(func.max(Sample.sample_code)).filter(
            Sample.sample_code.like(f"{prefix}%")
        ).scalar()
        
        if max_code:
            # Extract the numeric part
            try:
                seq_num = int(max_code.split('-')[-1])
                next_seq = seq_num + 1
            except (ValueError, IndexError):
                # If there are malformed codes, get the count instead
                count = db.session.query(Sample).filter(
                    Sample.sample_code.like(f"{prefix}%")
                ).count()
                next_seq = count + 1
        else:
            # No samples for this year yet - start from 1
            next_seq = 1
        
        # Format with zero-padding to 5 digits
        sample_code = f"SKS-{year}-{next_seq:05d}"
        
        return sample_code
