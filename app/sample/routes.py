"""
Sample routes module.

Provides endpoints for Sample CRUD operations and UI.
"""

from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from app.extensions import db
from app.models import Sample, FSO
from app.utils.lookup import lookup_fssai
from app.utils.fso_data import get_all_fso_names
from app.sample.sample_utils import generate_sample_code
from app.services.sheets_sync import sync_to_sheets

# Import the blueprint from __init__.py
from app.sample import sample_bp


# Sample types for dropdown
SAMPLE_TYPES = [
    'Food',
    'Water',
    'Oil',
    'Dairy',
    'Spices',
    'Beverage',
    'Packaged',
    'Other'
]


@sample_bp.route('/')
def index():
    """Sample entry form page."""
    fso_names = get_all_fso_names()
    return render_template('sample/index.html', 
                         fso_names=fso_names,
                         sample_types=SAMPLE_TYPES)


@sample_bp.route('/list')
def list_samples():
    """List all samples with pagination, sorting, and filtering."""
    # Get query parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    sort_by = request.args.get('sort_by', 'collection_date')
    sort_order = request.args.get('sort_order', 'desc')
    filter_fso = request.args.get('fso_name')
    filter_date_from = request.args.get('collection_date_from')
    filter_date_to = request.args.get('collection_date_to')
    
    # Base query
    query = Sample.query.join(FSO, Sample.fso_name == FSO.fso_name)
    
    # Apply filters
    if filter_fso:
        query = query.filter(Sample.fso_name == filter_fso)
    
    if filter_date_from:
        query = query.filter(Sample.collection_date >= filter_date_from)
    
    if filter_date_to:
        query = query.filter(Sample.collection_date <= filter_date_to)
    
    # Apply sorting
    if sort_by == 'collection_date':
        if sort_order == 'asc':
            query = query.order_by(Sample.collection_date.asc())
        else:
            query = query.order_by(Sample.collection_date.desc())
    elif sort_by == 'fso_name':
        if sort_order == 'asc':
            query = query.order_by(FSO.fso_name.asc())
        else:
            query = query.order_by(FSO.fso_name.desc())
    elif sort_by == 'sample_code':
        if sort_order == 'asc':
            query = query.order_by(Sample.sample_code.asc())
        else:
            query = query.order_by(Sample.sample_code.desc())
    else:
        query = query.order_by(Sample.collection_date.desc())
    
    # Paginate
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()
    
    return render_template('sample/list.html',
                         samples=paginated.items,
                         pagination=paginated,
                         fso_names=all_fso_names,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         filter_fso=filter_fso,
                         filter_date_from=filter_date_from,
                         filter_date_to=filter_date_to)


@sample_bp.route('/lookup_retailer', methods=['POST'])
def lookup_retailer():
    """Lookup retailer information by FSSAI number."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    fssai_number = data.get('retailer_fssai', '').strip()
    if not fssai_number:
        return jsonify({'error': 'FSSAI number is required'}), 400
    
    # Use existing lookup function
    result, error = lookup_fssai(fssai_number)
    
    if error:
        return jsonify({'error': error}), 404
    
    if result:
        return jsonify({
            'companyName': result.get('companyName'),
            'fullAddress': result.get('fullAddress'),
            'expiryDate': result.get('expiryDate'),
            'source': result.get('source')
        })
    
    return jsonify({'error': 'Retailer not found'}), 404


@sample_bp.route('/create', methods=['POST'])
def create_sample():
    """Create a new sample record."""
    form_data = request.form.to_dict()
    
    # Required fields
    sample_name = form_data.get('sample_name', '').strip()
    fso_name = form_data.get('fso_name', '').strip()
    collection_date = form_data.get('collection_date', '').strip()
    
    if not sample_name:
        return jsonify({'error': 'sample_name is required'}), 400
    if not fso_name:
        return jsonify({'error': 'fso_name is required'}), 400
    if not collection_date:
        return jsonify({'error': 'collection_date is required'}), 400
    
    # Validate FSO exists
    fso = FSO.query.get(fso_name)
    if not fso:
        return jsonify({'error': f'FSO "{fso_name}" not found in database'}), 400
    
    # Generate sample code
    sample_code = generate_sample_code()
    
    # Handle retailer autofill
    retailer_fssai = form_data.get('retailer_fssai', '').strip()
    retailer_name = form_data.get('retailer_name', '').strip()
    
    # If retailer_fssai is provided but retailer_name is empty, try to autofill
    if retailer_fssai and not retailer_name:
        result, error = lookup_fssai(retailer_fssai)
        if result and not error:
            retailer_name = result.get('companyName', retailer_fssai)
    
    # Create sample record
    sample = Sample(
        sample_code=sample_code,
        sample_name=sample_name,
        sample_type=form_data.get('sample_type', '').strip() or None,
        fso_name=fso_name,
        collection_date=collection_date,
        submission_date=form_data.get('submission_date', '').strip() or None,
        retailer_fssai=retailer_fssai or None,
        retailer_name=retailer_name or None,
        price=form_data.get('price', '').strip() or None,
        created_at=datetime.utcnow()
    )
    
    try:
        db.session.add(sample)
        db.session.commit()
        
        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                'id': sample.id,
                'sample_code': sample.sample_code,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type or '',
                'fso_name': sample.fso_name,
                'collection_date': sample.collection_date,
                'submission_date': sample.submission_date or '',
                'retailer_fssai': sample.retailer_fssai or '',
                'retailer_name': sample.retailer_name or '',
                'price': sample.price or '',
                'created_at': sample.created_at.isoformat() if sample.created_at else '',
                'synced_at': ''
            }
            success = sync_to_sheets("sample_repo", row_dict)
            if success:
                # Update synced_at timestamp
                sample.synced_at = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Sample Sheets sync failed: {e}")
        
        return jsonify({
            'message': 'Sample created successfully',
            'sample_id': sample.id,
            'sample_code': sample.sample_code
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create sample: {str(e)}'}), 500


@sample_bp.route('/<int:sample_id>', methods=['GET'])
def get_sample(sample_id):
    """Get a specific sample by ID."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({'error': f'Sample with id {sample_id} not found'}), 404
    
    return jsonify({
        'id': sample.id,
        'sample_code': sample.sample_code,
        'sample_name': sample.sample_name,
        'sample_type': sample.sample_type,
        'fso_name': sample.fso_name,
        'collection_date': sample.collection_date,
        'submission_date': sample.submission_date,
        'retailer_fssai': sample.retailer_fssai,
        'retailer_name': sample.retailer_name,
        'price': sample.price,
        'created_at': sample.created_at.isoformat() if sample.created_at else None,
        'synced_at': sample.synced_at.isoformat() if sample.synced_at else None
    })


@sample_bp.route('/<int:sample_id>', methods=['PUT'])
def update_sample(sample_id):
    """Update a sample record."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({'error': f'Sample with id {sample_id} not found'}), 404
    
    form_data = request.form.to_dict()
    
    # Update fields
    if 'sample_name' in form_data:
        sample.sample_name = form_data['sample_name'].strip()
    if 'sample_type' in form_data:
        sample.sample_type = form_data['sample_type'].strip() or None
    if 'fso_name' in form_data:
        fso_name = form_data['fso_name'].strip()
        # Validate FSO exists
        fso = FSO.query.get(fso_name)
        if not fso:
            return jsonify({'error': f'FSO "{fso_name}" not found'}), 400
        sample.fso_name = fso_name
    if 'collection_date' in form_data:
        sample.collection_date = form_data['collection_date'].strip()
    if 'submission_date' in form_data:
        sample.submission_date = form_data['submission_date'].strip() or None
    if 'retailer_fssai' in form_data:
        sample.retailer_fssai = form_data['retailer_fssai'].strip() or None
    if 'retailer_name' in form_data:
        sample.retailer_name = form_data['retailer_name'].strip() or None
    if 'price' in form_data:
        sample.price = form_data['price'].strip() or None
    
    try:
        db.session.commit()
        
        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                'id': sample.id,
                'sample_code': sample.sample_code,
                'sample_name': sample.sample_name,
                'sample_type': sample.sample_type or '',
                'fso_name': sample.fso_name,
                'collection_date': sample.collection_date,
                'submission_date': sample.submission_date or '',
                'retailer_fssai': sample.retailer_fssai or '',
                'retailer_name': sample.retailer_name or '',
                'price': sample.price or '',
                'created_at': sample.created_at.isoformat() if sample.created_at else '',
                'synced_at': datetime.utcnow().isoformat()
            }
            sync_to_sheets("sample_repo", row_dict)
        except Exception as e:
            current_app.logger.warning(f"Sample Sheets sync failed: {e}")
        
        return jsonify({'message': 'Sample updated successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update sample: {str(e)}'}), 500


@sample_bp.route('/<int:sample_id>', methods=['DELETE'])
def delete_sample(sample_id):
    """Delete a sample record."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({'error': f'Sample with id {sample_id} not found'}), 404
    
    try:
        db.session.delete(sample)
        db.session.commit()
        return jsonify({'message': 'Sample deleted successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete sample: {str(e)}'}), 500
