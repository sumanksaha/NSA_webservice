from datetime import datetime
import json
from flask import Blueprint, request, jsonify
from app.extensions import db
from app.models import FboIssue, FboIssueAudit
from app.utils.lookup import lookup_fssai

fbo_issue_bp = Blueprint(
    'fbo_issue',
    __name__,
    template_folder='templates',
    static_folder='static'
)

# Valid state transitions: from_state -> [valid to_state values]
VALID_TRANSITIONS = {
    'open': ['permission_pending', 'dismissed'],
    'permission_pending': ['permission_granted', 'dismissed'],
    'permission_granted': ['closed'],
    'closed': [],
    'dismissed': [],
}

# Sample-specific fields required in detail_json
SAMPLE_REQUIRED_FIELDS = {'sampling_date', 'sample_name', 'price', 'sample_code'}

# Inspection-specific fields required in detail_json
INSPECTION_REQUIRED_FIELDS = {'checklist'}


def validate_detail_json(source_type, detail_json):
    """
    Validate that detail_json contains the required fields for the given source_type.
    Returns (is_valid, error_message)
    """
    if detail_json is None:
        return False, "detail_json is required"
    
    try:
        if isinstance(detail_json, str):
            detail = json.loads(detail_json) if detail_json else {}
        elif isinstance(detail_json, dict):
            detail = detail_json
        else:
            return False, f"detail_json must be string or dict, got {type(detail_json).__name__}"
    except Exception as e:
        return False, f"Invalid JSON in detail_json: {str(e)}"
    
    if source_type == 'sample':
        missing = SAMPLE_REQUIRED_FIELDS - set(detail.keys())
        if missing:
            return False, f"Missing required fields for sample: {sorted(missing)}"
        return True, None
    
    elif source_type == 'inspection':
        missing = INSPECTION_REQUIRED_FIELDS - set(detail.keys())
        if missing:
            return False, f"Missing required fields for inspection: {sorted(missing)}"
        return True, None
    
    return False, f"Unknown source_type: {source_type}"


@fbo_issue_bp.route('/new', methods=['POST'])
def create_issue():
    """
    Create a new FBO issue.
    Required fields: source_type, fbo_id, fso_name
    Optional: manufacturer_fbo_id (only valid for sample source_type), fbo_name
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    # Validate required fields
    source_type = data.get('source_type')
    fbo_id = data.get('fbo_id')
    fso_name = data.get('fso_name')
    fbo_name = data.get('fbo_name', '')
    detail_json = data.get('detail_json')
    manufacturer_fbo_id = data.get('manufacturer_fbo_id')
    
    if not source_type:
        return jsonify({'error': 'source_type is required'}), 400
    if source_type not in ('inspection', 'sample'):
        return jsonify({'error': "source_type must be 'inspection' or 'sample'"}), 400
    if not fbo_id:
        return jsonify({'error': 'fbo_id is required'}), 400
    if not fso_name:
        return jsonify({'error': 'fso_name is required'}), 400
    
    # Validate manufacturer_fbo_id: only allowed for sample, must be null for inspection
    if source_type == 'inspection' and manufacturer_fbo_id:
        return jsonify({'error': 'manufacturer_fbo_id must be null for inspection source_type'}), 400
    
    # Validate manufacturer_fbo_id: if provided for sample, it must be a valid FBO ID
    if source_type == 'sample' and manufacturer_fbo_id:
        mfg_result, mfg_error = lookup_fssai(manufacturer_fbo_id)
        if mfg_error:
            return jsonify({'error': f'Invalid manufacturer_fbo_id: {mfg_error}'}), 400
    
    # Lookup the primary fbo_id
    fbo_result, fbo_error = lookup_fssai(fbo_id)
    if fbo_error:
        return jsonify({'error': f'Invalid fbo_id: {fbo_error}'}), 400
    
    # Use the resolved fbo_name if not provided
    if not fbo_name and fbo_result:
        fbo_name = fbo_result.get('companyName', fbo_id)
    
    # Validate detail_json matches source_type
    is_valid, error_msg = validate_detail_json(source_type, detail_json)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    # Start transaction
    try:
        # Create FboIssue row
        new_issue = FboIssue(
            fbo_id=fbo_id,
            manufacturer_fbo_id=manufacturer_fbo_id,
            fbo_name=fbo_name,
            source_type=source_type,
            state='open',
            fso_name=fso_name,
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat(),
            detail_json=json.dumps(detail_json) if detail_json else None
        )
        
        db.session.add(new_issue)
        db.session.flush()  # Get the id
        
        # Create corresponding audit row
        new_audit = FboIssueAudit(
            issue_id=new_issue.id,
            from_state=None,
            to_state='open',
            asserted_by=fso_name,
            asserted_at=datetime.utcnow().isoformat(),
            note=f'Initial creation: source_type={source_type}, fbo_id={fbo_id}'
        )
        db.session.add(new_audit)
        
        db.session.commit()
        
        return jsonify({
            'message': 'FBO issue created successfully',
            'issue_id': new_issue.id,
            'fbo_id': new_issue.fbo_id,
            'state': new_issue.state,
            'created_at': new_issue.created_at
        }), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create issue: {str(e)}'}), 500


@fbo_issue_bp.route('/<int:issue_id>/transition', methods=['POST'])
def transition_issue(issue_id):
    """
    Transition an FBO issue to a new state.
    Required fields: to_state, asserted_by
    Optional: note
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body must be JSON'}), 400
    
    to_state = data.get('to_state')
    asserted_by = data.get('asserted_by')
    note = data.get('note', '')
    
    if not to_state:
        return jsonify({'error': 'to_state is required'}), 400
    if not asserted_by:
        return jsonify({'error': 'asserted_by is required'}), 400
    if to_state not in ('open', 'permission_pending', 'permission_granted', 'closed', 'dismissed'):
        return jsonify({'error': f"Invalid to_state: {to_state}. Must be one of 'open', 'permission_pending', 'permission_granted', 'closed', 'dismissed'"}), 400
    
    # Get current issue
    issue = db.session.get(FboIssue, issue_id)
    if not issue:
        return jsonify({'error': f'FBO issue with id {issue_id} not found'}), 404
    
    from_state = issue.state
    
    # Check if transition is valid
    valid_targets = VALID_TRANSITIONS.get(from_state, [])
    if to_state not in valid_targets:
        return jsonify({
            'error': f'Invalid state transition: {from_state} -> {to_state}. '
                     f'Valid transitions from {from_state}: {valid_targets}'
        }), 400
    
    # Enforce DB CHECK constraints at app layer for clean error messages
    # Check: NOT (source_type = 'sample' AND state = 'dismissed')
    if issue.source_type == 'sample' and to_state == 'dismissed':
        return jsonify({
            'error': 'Cannot transition sample source_type to dismissed state'
        }), 400
    
    # Start transaction
    try:
        # Update issue state
        issue.state = to_state
        issue.updated_at = datetime.utcnow().isoformat()
        db.session.add(issue)
        
        # Create audit row
        new_audit = FboIssueAudit(
            issue_id=issue.id,
            from_state=from_state,
            to_state=to_state,
            asserted_by=asserted_by,
            asserted_at=datetime.utcnow().isoformat(),
            note=note
        )
        db.session.add(new_audit)
        
        db.session.commit()
        
        return jsonify({
            'message': 'State transition successful',
            'issue_id': issue.id,
            'from_state': from_state,
            'to_state': to_state,
            'updated_at': issue.updated_at
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to transition issue: {str(e)}'}), 500


@fbo_issue_bp.route('/<int:issue_id>', methods=['GET'])
def get_issue(issue_id):
    """
    Get full details of an FBO issue including its audit history.
    """
    issue = db.session.get(FboIssue, issue_id)
    if not issue:
        return jsonify({'error': f'FBO issue with id {issue_id} not found'}), 404
    
    # Get audit history
    audit_history = FboIssueAudit.query.filter_by(issue_id=issue.id).order_by(FboIssueAudit.asserted_at).all()
    
    # Parse detail_json
    detail = None
    if issue.detail_json:
        try:
            detail = json.loads(issue.detail_json)
        except Exception:
            detail = issue.detail_json
    
    # Build audit history list
    audits = []
    for audit in audit_history:
        audits.append({
            'id': audit.id,
            'from_state': audit.from_state,
            'to_state': audit.to_state,
            'asserted_by': audit.asserted_by,
            'asserted_at': audit.asserted_at,
            'note': audit.note
        })
    
    return jsonify({
        'id': issue.id,
        'fbo_id': issue.fbo_id,
        'manufacturer_fbo_id': issue.manufacturer_fbo_id,
        'fbo_name': issue.fbo_name,
        'source_type': issue.source_type,
        'state': issue.state,
        'fso_name': issue.fso_name,
        'created_at': issue.created_at,
        'updated_at': issue.updated_at,
        'detail': detail,
        'audit_history': audits
    }), 200


@fbo_issue_bp.route('/', methods=['GET'])
def list_issues():
    """
    List FBO issues with optional filtering by fbo_id or state.
    Query params: fbo_id, state
    """
    fbo_id = request.args.get('fbo_id')
    state = request.args.get('state')
    
    query = FboIssue.query
    
    if fbo_id:
        query = query.filter_by(fbo_id=fbo_id)
    if state:
        query = query.filter_by(state=state)
    
    issues = query.all()
    
    result = []
    for issue in issues:
        detail = None
        if issue.detail_json:
            try:
                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json
        
        result.append({
            'id': issue.id,
            'fbo_id': issue.fbo_id,
            'manufacturer_fbo_id': issue.manufacturer_fbo_id,
            'fbo_name': issue.fbo_name,
            'source_type': issue.source_type,
            'state': issue.state,
            'fso_name': issue.fso_name,
            'created_at': issue.created_at,
            'updated_at': issue.updated_at,
            'detail': detail
        })
    
    return jsonify(result), 200
