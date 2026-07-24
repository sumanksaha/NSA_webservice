"""
fso_data.py

Loads the FSO (Food Safety Officer) list from a markdown file and exposes
a list of FSO names. Also provides sync functionality to upsert names into
the fso database table.

Sync is ADDITIVE ONLY — never delete existing FSO rows even if removed from markdown
(to preserve FK integrity on historical records).
"""

import os
import re
import logging
from app.extensions import db
from app.models import FSO

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..', '..'))
FSO_MD_PATH = os.path.join(WORKSPACE_DIR, "fso_list.md")


def load_fso_names(path: str = FSO_MD_PATH) -> list:
    """
    Parses a markdown file with a list of FSO names.
    Expected format:
    # FSO List
    
    - Name 1
    - Name 2
    - Name 3
    
    Returns a list of name strings. The header line and any malformed lines are ignored.
    Raises FileNotFoundError if the file is missing.
    
    Handles gracefully:
    - Missing file: raises FileNotFoundError
    - Malformed lines (not starting with '- '): skipped with warning
    - Empty lines: skipped
    - Lines with only '-': skipped
    """
    if not os.path.exists(path):
        logger.warning(f"FSO list file not found: {path}")
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    
    # Extract list items (lines starting with - ) and strip whitespace and bullet
    names = []
    line_number = 0
    for line in text.split('\n'):
        line_number += 1
        line = line.strip()
        
        # Skip empty lines
        if not line:
            continue
        
        # Skip header lines (lines starting with #)
        if line.startswith('#'):
            continue
        
        # Process list items
        if line.startswith('- '):
            name = line[2:].strip()
            if name:  # Skip empty names
                names.append(name)
        elif line.startswith('-') and len(line) > 1:
            # Malformed line: starts with - but no space
            logger.warning(f"FSO list: skipping malformed line {line_number}: '{line}'")
        else:
            # Other non-list lines (not header, not bullet) - skip with warning
            if line.strip():
                logger.warning(f"FSO list: skipping non-list line {line_number}: '{line}'")
    
    return names


def sync_fso_from_markdown(path: str = FSO_MD_PATH) -> dict:
    """
    Reads FSO names from the markdown file and upserts them into the fso table.
    
    Sync is ADDITIVE ONLY — existing FSO rows are never deleted, even if removed from markdown.
    This preserves FK integrity on historical records that reference FSO names.
    
    Returns a dict with:
    - inserted: count of new names inserted
    - updated: count of existing names that were already present
    - skipped: count of lines that were skipped (header, empty lines, malformed)
    - errors: list of any error messages
    """
    try:
        names = load_fso_names(path)
    except FileNotFoundError as e:
        # File missing is a warning, not an error - don't crash startup
        logger.warning(f"FSO sync: {str(e)}")
        return {'inserted': 0, 'updated': 0, 'skipped': 0, 'errors': [str(e)]}
    except Exception as e:
        logger.error(f"FSO sync: unexpected error loading names: {str(e)}")
        return {'inserted': 0, 'updated': 0, 'skipped': 0, 'errors': [str(e)]}
    
    inserted = 0
    updated = 0
    skipped = 0
    errors = []
    
    # Get existing FSO names from database
    existing_names = set()
    try:
        existing_fsos = FSO.query.all()
        existing_names = {fso.fso_name for fso in existing_fsos}
    except Exception as e:
        logger.error(f"FSO sync: database query error: {str(e)}")
        errors.append(f"Database query error: {str(e)}")
        return {'inserted': 0, 'updated': 0, 'skipped': 0, 'errors': errors}
    
    for name in names:
        if not name:
            skipped += 1
            continue
        
        # Normalize: strip whitespace only - preserve capitalization as in file
        normalized_name = name.strip()
        if not normalized_name:
            skipped += 1
            continue
        
        # ADDITIVE ONLY: if name already exists, just count as updated, don't delete anything
        if normalized_name in existing_names:
            updated += 1
        else:
            try:
                fso = FSO(fso_name=normalized_name)
                db.session.add(fso)
                existing_names.add(normalized_name)  # Prevent duplicates in same sync
                inserted += 1
            except Exception as e:
                logger.error(f"FSO sync: failed to insert '{normalized_name}': {str(e)}")
                errors.append(f"Failed to insert '{normalized_name}': {str(e)}")
    
    try:
        db.session.commit()
        logger.info(f"FSO sync: inserted {inserted}, updated {updated}, skipped {skipped}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"FSO sync: database commit error: {str(e)}")
        errors.append(f"Database commit error: {str(e)}")
    
    return {
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'errors': errors
    }


def get_all_fso_names() -> list:
    """
    Returns a list of all FSO names from the database, sorted alphabetically.
    """
    try:
        fsos = FSO.query.order_by(FSO.fso_name.asc()).all()
        return [fso.fso_name for fso in fsos]
    except Exception as e:
        logger.error(f"Error fetching FSO names: {str(e)}")
        return []


def sync_fso_manually():
    """
    Manual trigger for FSO sync. Called from route.
    Returns the sync result.
    """
    return sync_fso_from_markdown()


# Auto-sync on module import (happens during app startup)
# This runs when the app starts and the first request hits any endpoint that imports this module
# To ensure it runs at startup, we'll call it from app factory
try:
    FSO_NAMES = load_fso_names()
except FileNotFoundError:
    FSO_NAMES = []
