"""
Inspection routes module.

Provides endpoints for Inspection CRUD operations and UI.
"""

from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from app.extensions import db
from app.models import Inspection, FSO, Adjudication
from app.utils.lookup import lookup_fssai, lookup_ce
from app.utils.fso_data import get_all_fso_names
from app.inspection.inspection_utils import generate_inspection_code, calculate_compliance_deadline
from app.services.sheets_sync import sync_to_sheets

# Import the blueprint from __init__.py
from app.inspection import inspection_bp


@inspection_bp.route('/')
def index():
    """Inspection entry form page."""
    fso_names = get_all_fso_names()
    return render_template('inspection/index.html', 
                         fso_names=fso_names)


@inspection_bp.route('/list')
def list_inspections():
    """List all inspections with pagination, sorting, and filtering."""
    # Get query parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    sort_by = request.args.get('sort_by', 'inspection_date')
    sort_order = request.args.get('sort_order', 'desc')
    filter_fso = request.args.get('fso_name')
    filter_date_from = request.args.get('inspection_date_from')
    filter_date_to = request.args.get('inspection_date_to')

    # Base query
    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name)

    # Apply filters
    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    if filter_date_from:
        query = query.filter(Inspection.inspection_date >= filter_date_from)

    if filter_date_to:
        query = query.filter(Inspection.inspection_date <= filter_date_to)

    # Apply sorting
    if sort_by == 'inspection_date':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_date.asc())
        else:
            query = query.order_by(Inspection.inspection_date.desc())
    elif sort_by == 'compliance_deadline':
        if sort_order == 'asc':
            query = query.order_by(Inspection.compliance_deadline.asc())
        else:
            query = query.order_by(Inspection.compliance_deadline.desc())
    elif sort_by == 'fso_name':
        if sort_order == 'asc':
            query = query.order_by(FSO.fso_name.asc())
        else:
            query = query.order_by(FSO.fso_name.desc())
    elif sort_by == 'inspection_code':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_code.asc())
        else:
            query = query.order_by(Inspection.inspection_code.desc())
    else:
        query = query.order_by(Inspection.inspection_date.desc())

    # Paginate
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()

    return render_template('inspection/list.html',
                         inspections=paginated.items,
                         pagination=paginated,
                         fso_names=all_fso_names,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         filter_fso=filter_fso,
                         filter_date_from=filter_date_from,
                         filter_date_to=filter_date_to)


@inspection_bp.route('/lookup_fssai', methods=['POST'])
def lookup_fssai_route():
    """Lookup FSSAI license information."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    fssai_number = data.get('fssai_license', '').strip()
    if not fssai_number:
        return jsonify({'error': 'FSSAI license number is required'}), 400

    # Use existing lookup function
    result, error = lookup_fssai(fssai_number)

    if error:
        return jsonify({'error': error, 'source': 'fssai'}), 404

    if result:
        return jsonify({
            'fbo_name': result.get('companyName'),
            'fbo_address': result.get('fullAddress'),
            'expiry_date': result.get('expiryDate'),
            'source': result.get('source')
        })

    return jsonify({'error': 'FSSAI license not found'}), 404


@inspection_bp.route('/lookup_ce', methods=['POST'])
def lookup_ce_route():
    """Lookup CE (KMC Trade) license information."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    ce_number = data.get('ce_license_no', '').strip()
    if not ce_number:
        return jsonify({'error': 'CE license number is required'}), 400

    # Use existing lookup function
    try:
        result = lookup_ce(ce_number)
    except Exception as e:
        return jsonify({'error': f'KMC lookup failed: {str(e)}'}), 502

    if not result:
        return jsonify({'error': 'CE license not found'}), 404

    # Return the full result with identity for consistency with adjudication
    return jsonify(result)


@inspection_bp.route('/create', methods=['POST'])
def create_inspection():
    """Create a new inspection record."""
    form_data = request.form.to_dict()

    # Required fields - using canonical keys from Step 2
    food_safety_officer_name = form_data.get('food_safety_officer_name', '').strip()
    inspection_date = form_data.get('inspection_date', '').strip()

    if not food_safety_officer_name:
        return jsonify({'error': 'food_safety_officer_name is required'}), 400
    if not inspection_date:
        return jsonify({'error': 'inspection_date is required'}), 400

    # Validate FSO exists - map canonical to DB column
    fso = FSO.query.get(food_safety_officer_name)
    if not fso:
        return jsonify({'error': f'FSO "{food_safety_officer_name}" not found in database'}), 400

    # Generate inspection code
    inspection_code = generate_inspection_code()

    # Calculate compliance deadline
    compliance_deadline = form_data.get('compliance_deadline', '').strip()
    if not compliance_deadline:
        # Auto-calculate if not provided
        compliance_deadline = calculate_compliance_deadline(inspection_date)

    # Get form fields
    fssai_license = form_data.get('fssai_license', '').strip() or None
    ce_license_no = form_data.get('ce_license_no', '').strip() or None
    fbo_name = form_data.get('fbo_name', '').strip() or None
    fbo_address = form_data.get('fbo_address', '').strip() or None
    concerned_food = form_data.get('concerned_food', '').strip() or None
    problem = form_data.get('problem', '').strip() or None

    # Create inspection record
    # Map canonical keys to DB columns: food_safety_officer_name -> fso_name (FK)
    inspection = Inspection(
        inspection_code=inspection_code,
        fso_name=food_safety_officer_name,
        fssai_license=fssai_license,
        ce_license_no=ce_license_no,
        fbo_name=fbo_name,
        fbo_address=fbo_address,
        concerned_food=concerned_food,
        problem=problem,
        inspection_date=inspection_date,
        compliance_deadline=compliance_deadline,
        is_dismissed=False,
        created_at=datetime.utcnow()
    )

    try:
        db.session.add(inspection)
        db.session.commit()
        
        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                'id': inspection.id,
                'inspection_code': inspection.inspection_code,
                'fso_name': inspection.fso_name,
                'fssai_license': inspection.fssai_license or '',
                'ce_license_no': inspection.ce_license_no or '',
                'fbo_name': inspection.fbo_name or '',
                'fbo_address': inspection.fbo_address or '',
                'concerned_food': inspection.concerned_food or '',
                'problem': inspection.problem or '',
                'inspection_date': inspection.inspection_date,
                'compliance_deadline': inspection.compliance_deadline,
                'is_dismissed': str(inspection.is_dismissed),
                'dismissed_by': inspection.dismissed_by or '',
                'adjudication_id': str(inspection.adjudication_id or ''),
                'created_at': inspection.created_at.isoformat() if inspection.created_at else '',
                'synced_at': ''
            }
            success = sync_to_sheets("inspection_log", row_dict)
            if success:
                # Update synced_at timestamp
                inspection.synced_at = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")
        
        return jsonify({
            'message': 'Inspection created successfully',
            'inspection_id': inspection.id,
            'inspection_code': inspection.inspection_code
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create inspection: {str(e)}'}), 500


@inspection_bp.route('/<int:inspection_id>', methods=['GET'])
def get_inspection(inspection_id):
    """Get a specific inspection by ID."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404

    return jsonify({
        'id': inspection.id,
        'inspection_code': inspection.inspection_code,
        'fso_name': inspection.fso_name,
        'fssai_license': inspection.fssai_license,
        'ce_license_no': inspection.ce_license_no,
        'fbo_name': inspection.fbo_name,
        'fbo_address': inspection.fbo_address,
        'concerned_food': inspection.concerned_food,
        'problem': inspection.problem,
        'inspection_date': inspection.inspection_date,
        'compliance_deadline': inspection.compliance_deadline,
        'is_dismissed': inspection.is_dismissed,
        'dismissed_by': inspection.dismissed_by,
        'dismissed_at': inspection.dismissed_at.isoformat() if inspection.dismissed_at else None,
        'adjudication_id': inspection.adjudication_id,
        'created_at': inspection.created_at.isoformat() if inspection.created_at else None,
        'synced_at': inspection.synced_at.isoformat() if inspection.synced_at else None
    })


@inspection_bp.route('/<int:inspection_id>', methods=['PUT'])
def update_inspection(inspection_id):
    """Update an inspection record."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404

    form_data = request.form.to_dict()

    # Update fields
    # Map canonical key to DB column: food_safety_officer_name -> fso_name
    if 'food_safety_officer_name' in form_data:
        food_safety_officer_name = form_data['food_safety_officer_name'].strip()
        # Validate FSO exists
        fso = FSO.query.get(food_safety_officer_name)
        if not fso:
            return jsonify({'error': f'FSO "{food_safety_officer_name}" not found'}), 400
        inspection.fso_name = food_safety_officer_name

    if 'fssai_license' in form_data:
        inspection.fssai_license = form_data['fssai_license'].strip() or None
    if 'ce_license_no' in form_data:
        inspection.ce_license_no = form_data['ce_license_no'].strip() or None
    if 'fbo_name' in form_data:
        inspection.fbo_name = form_data['fbo_name'].strip() or None
    if 'fbo_address' in form_data:
        inspection.fbo_address = form_data['fbo_address'].strip() or None
    if 'concerned_food' in form_data:
        inspection.concerned_food = form_data['concerned_food'].strip() or None
    if 'problem' in form_data:
        inspection.problem = form_data['problem'].strip() or None
    if 'inspection_date' in form_data:
        inspection.inspection_date = form_data['inspection_date'].strip()
        # Recalculate compliance deadline if inspection_date changes and compliance_deadline is not provided
        if 'compliance_deadline' not in form_data or not form_data.get('compliance_deadline', '').strip():
            inspection.compliance_deadline = calculate_compliance_deadline(inspection.inspection_date)
    if 'compliance_deadline' in form_data:
        inspection.compliance_deadline = form_data['compliance_deadline'].strip()

    try:
        db.session.commit()
        
        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                'id': inspection.id,
                'inspection_code': inspection.inspection_code,
                'fso_name': inspection.fso_name,
                'fssai_license': inspection.fssai_license or '',
                'ce_license_no': inspection.ce_license_no or '',
                'fbo_name': inspection.fbo_name or '',
                'fbo_address': inspection.fbo_address or '',
                'concerned_food': inspection.concerned_food or '',
                'problem': inspection.problem or '',
                'inspection_date': inspection.inspection_date,
                'compliance_deadline': inspection.compliance_deadline,
                'is_dismissed': str(inspection.is_dismissed),
                'dismissed_by': inspection.dismissed_by or '',
                'adjudication_id': str(inspection.adjudication_id or ''),
                'created_at': inspection.created_at.isoformat() if inspection.created_at else '',
                'synced_at': datetime.utcnow().isoformat()
            }
            sync_to_sheets("inspection_log", row_dict)
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")
        
        return jsonify({'message': 'Inspection updated successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update inspection: {str(e)}'}), 500


@inspection_bp.route('/<int:inspection_id>', methods=['DELETE'])
def delete_inspection(inspection_id):
    """Delete an inspection record."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404

    try:
        db.session.delete(inspection)
        db.session.commit()
        return jsonify({'message': 'Inspection deleted successfully'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete inspection: {str(e)}'}), 500


# ============================================================================
# Step 4: Derived-State Views and Actions
# ============================================================================

@inspection_bp.route('/open')
def open_issues():
    """Open Issues view: inspections where compliance_deadline >= today AND is_dismissed = false AND adjudication_id IS NULL."""
    today = date.today().isoformat()
    
    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()
    
    # Get query parameters for sorting
    sort_by = request.args.get('sort_by', 'compliance_deadline')
    sort_order = request.args.get('sort_order', 'asc')
    filter_fso = request.args.get('fso_name')
    
    # Base query for Open Issues
    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        Inspection.compliance_deadline >= today,
        Inspection.is_dismissed == False,
        Inspection.adjudication_id.is_(None)
    )
    
    # Apply FSO filter if provided
    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)
    
    # Apply sorting
    if sort_by == 'compliance_deadline':
        if sort_order == 'asc':
            query = query.order_by(Inspection.compliance_deadline.asc())
        else:
            query = query.order_by(Inspection.compliance_deadline.desc())
    elif sort_by == 'inspection_code':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_code.asc())
        else:
            query = query.order_by(Inspection.inspection_code.desc())
    elif sort_by == 'fso_name':
        if sort_order == 'asc':
            query = query.order_by(FSO.fso_name.asc())
        else:
            query = query.order_by(FSO.fso_name.desc())
    elif sort_by == 'inspection_date':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_date.asc())
        else:
            query = query.order_by(Inspection.inspection_date.desc())
    else:
        query = query.order_by(Inspection.compliance_deadline.asc())
    
    inspections = query.all()
    
    return render_template('inspection/open_issues.html',
                         inspections=inspections,
                         fso_names=all_fso_names,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         filter_fso=filter_fso,
                         view_type='open')


@inspection_bp.route('/pending')
def pending_action():
    """Pending Action view: inspections where compliance_deadline < today AND is_dismissed = false AND adjudication_id IS NULL."""
    today = date.today().isoformat()
    
    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()
    
    # Get query parameters for sorting
    sort_by = request.args.get('sort_by', 'compliance_deadline')
    sort_order = request.args.get('sort_order', 'asc')
    filter_fso = request.args.get('fso_name')
    
    # Base query for Pending Action
    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        Inspection.compliance_deadline < today,
        Inspection.is_dismissed == False,
        Inspection.adjudication_id.is_(None)
    )
    
    # Apply FSO filter if provided
    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)
    
    # Apply sorting
    if sort_by == 'compliance_deadline':
        if sort_order == 'asc':
            query = query.order_by(Inspection.compliance_deadline.asc())
        else:
            query = query.order_by(Inspection.compliance_deadline.desc())
    elif sort_by == 'inspection_code':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_code.asc())
        else:
            query = query.order_by(Inspection.inspection_code.desc())
    elif sort_by == 'fso_name':
        if sort_order == 'asc':
            query = query.order_by(FSO.fso_name.asc())
        else:
            query = query.order_by(FSO.fso_name.desc())
    elif sort_by == 'inspection_date':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_date.asc())
        else:
            query = query.order_by(Inspection.inspection_date.desc())
    else:
        query = query.order_by(Inspection.compliance_deadline.asc())
    
    inspections = query.all()
    
    # Calculate days overdue for each inspection
    for inspection in inspections:
        deadline = datetime.strptime(inspection.compliance_deadline, '%Y-%m-%d').date()
        today_date = date.today()
        inspection.days_overdue = (today_date - deadline).days
    
    return render_template('inspection/pending_action.html',
                         inspections=inspections,
                         fso_names=all_fso_names,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         filter_fso=filter_fso,
                         view_type='pending')


@inspection_bp.route('/history')
def history():
    """History view: inspections that are dismissed or have adjudication_id set."""
    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()
    
    # Get query parameters for sorting
    sort_by = request.args.get('sort_by', 'dismissed_at')
    sort_order = request.args.get('sort_order', 'desc')
    filter_fso = request.args.get('fso_name')
    filter_type = request.args.get('type', 'all')  # 'all', 'dismissed', 'adjudicated'
    
    # Base query for closed/history inspections
    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        (Inspection.is_dismissed == True) | (Inspection.adjudication_id.isnot(None))
    )
    
    # Apply type filter
    if filter_type == 'dismissed':
        query = query.filter(Inspection.is_dismissed == True)
    elif filter_type == 'adjudicated':
        query = query.filter(Inspection.adjudication_id.isnot(None))
    
    # Apply FSO filter if provided
    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)
    
    # Apply sorting
    if sort_by == 'dismissed_at':
        if sort_order == 'asc':
            query = query.order_by(Inspection.dismissed_at.asc())
        else:
            query = query.order_by(Inspection.dismissed_at.desc())
    elif sort_by == 'inspection_code':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_code.asc())
        else:
            query = query.order_by(Inspection.inspection_code.desc())
    elif sort_by == 'fso_name':
        if sort_order == 'asc':
            query = query.order_by(FSO.fso_name.asc())
        else:
            query = query.order_by(FSO.fso_name.desc())
    elif sort_by == 'inspection_date':
        if sort_order == 'asc':
            query = query.order_by(Inspection.inspection_date.asc())
        else:
            query = query.order_by(Inspection.inspection_date.desc())
    else:
        query = query.order_by(Inspection.dismissed_at.desc())
    
    inspections = query.all()
    
    return render_template('inspection/history.html',
                         inspections=inspections,
                         fso_names=all_fso_names,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         filter_fso=filter_fso,
                         filter_type=filter_type,
                         view_type='history')


@inspection_bp.route('/<int:inspection_id>/dismiss', methods=['POST'])
def dismiss_inspection(inspection_id):
    """Dismiss an inspection (Pending Action only)."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404
    
    # Verify this is a Pending Action inspection
    today = date.today().isoformat()
    if inspection.compliance_deadline >= today:
        return jsonify({'error': 'Only Pending Action inspections (past deadline) can be dismissed'}), 400
    
    if inspection.is_dismissed:
        return jsonify({'error': 'Inspection is already dismissed'}), 400
    
    if inspection.adjudication_id:
        return jsonify({'error': 'Inspection already linked to adjudication'}), 400
    
    # Get dismissed_by from form data, default to inspection's FSO if not provided
    dismissed_by = request.form.get('dismissed_by', inspection.fso_name)
    
    # Update inspection
    inspection.is_dismissed = True
    inspection.dismissed_by = dismissed_by
    inspection.dismissed_at = datetime.utcnow()
    
    try:
        db.session.commit()
        return jsonify({
            'message': 'Inspection dismissed successfully',
            'inspection_id': inspection.id,
            'inspection_code': inspection.inspection_code
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to dismiss inspection: {str(e)}'}), 500


@inspection_bp.route('/<int:inspection_id>/create_adjudication', methods=['GET'])
def create_adjudication_from_inspection(inspection_id):
    """Redirect to Adjudication form with prefill data from inspection."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404
    
    # Verify this is a Pending Action inspection
    today = date.today().isoformat()
    if inspection.compliance_deadline >= today:
        return jsonify({'error': 'Only Pending Action inspections (past deadline) can create adjudication'}), 400
    
    if inspection.is_dismissed:
        return jsonify({'error': 'Dismissed inspections cannot create adjudication'}), 400
    
    if inspection.adjudication_id:
        return jsonify({'error': 'Inspection already linked to adjudication'}), 400
    
    # Build prefill query parameters - using canonical keys for Step 3
    # Semantic mapping: Inspection.inspection_date -> adjudication.first_inspection_date
    prefill = {
        'from_inspection': inspection_id,
        'food_safety_officer_name': inspection.fso_name,  # canonical
        'fbo_name': inspection.fbo_name or '',
        'fbo_address': inspection.fbo_address or '',
        'fssai_license': inspection.fssai_license or '',
        'ce_license_no': inspection.ce_license_no or '',
        'first_inspection_date': inspection.inspection_date,  # canonical: inspection date -> first inspection
        'compliance_deadline': inspection.compliance_deadline,
        # Do NOT set followup_inspection_date from inspection - leave for user
        'concerned_food': inspection.concerned_food or '',
        'problem': inspection.problem or '',
    }
    
    # Redirect to adjudication form with prefill data
    # We'll use GET parameters to pass the prefill data
    return redirect(url_for('adjudication.index', **prefill))


@inspection_bp.route('/<int:inspection_id>/link_adjudication/<int:adjudication_id>', methods=['POST'])
def link_adjudication(inspection_id, adjudication_id):
    """Link an inspection to an adjudication after successful save."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({'error': f'Inspection with id {inspection_id} not found'}), 404
    
    adjudication = Adjudication.query.get(adjudication_id)
    if not adjudication:
        return jsonify({'error': f'Adjudication with id {adjudication_id} not found'}), 404
    
    # Verify inspection is eligible for linking
    if inspection.is_dismissed:
        return jsonify({'error': 'Dismissed inspections cannot be linked to adjudication'}), 400
    
    if inspection.adjudication_id:
        return jsonify({'error': 'Inspection already linked to adjudication'}), 400
    
    # Link the inspection to the adjudication
    inspection.adjudication_id = adjudication_id
    
    try:
        db.session.commit()
        return jsonify({
            'message': 'Inspection linked to adjudication successfully',
            'inspection_id': inspection.id,
            'adjudication_id': adjudication.id
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to link inspection to adjudication: {str(e)}'}), 500
