#!/usr/bin/env python
"""Comprehensive tests for FBOIssue module (routes, state machine, audit trail)."""

import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from flask import Flask
from app.extensions import db
from app.models import FboIssue, FboIssueAudit
from app.fbo_issue.routes import fbo_issue_bp


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def app_with_temp_db():
    """Create Flask app with in-memory SQLite database."""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['TESTING'] = True
    
    db.init_app(app)
    app.register_blueprint(fbo_issue_bp, url_prefix='/fbo-issue')
    
    with app.app_context():
        db.create_all()
    
    yield app


@pytest.fixture
def client(app_with_temp_db):
    """Create test client for the app."""
    with app_with_temp_db.test_client() as client:
        with app_with_temp_db.app_context():
            yield client


@pytest.fixture
def mock_lookup_fssai(monkeypatch):
    """Mock lookup_fssai to return success for known FBO IDs."""
    def mock_lookup(license_no):
        if license_no and (license_no.startswith('1') or license_no.startswith('2')):
            return {
                'companyName': f'Test Company {license_no}',
                'fullAddress': f'Test Address {license_no}',
                'expiryDate': '2026-12-31',
                'source': 'license_data' if license_no.startswith('1') else 'registration_data'
            }, None
        return None, f"License/Registration number not found: {license_no}"
    
    import app.utils.lookup as lookup_module
    monkeypatch.setattr(lookup_module, 'lookup_fssai', mock_lookup)
    import app.fbo_issue.routes as routes_module
    monkeypatch.setattr(routes_module, 'lookup_fssai', mock_lookup)


# ============================================================================
# TEST GROUP A: Creation - valid cases
# ============================================================================

class TestCreationValid:
    def test_create_inspection_issue(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000001',
            'fso_name': 'Test FSO',
            'detail_json': {'checklist': ['item1', 'item2']}
        }
        with client.application.app_context():
            assert db.session.query(FboIssue).count() == 0
            assert db.session.query(FboIssueAudit).count() == 0
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 201
        resp_data = response.get_json()
        assert resp_data['message'] == 'FBO issue created successfully'
        assert resp_data['state'] == 'open'
        issue_id = resp_data['issue_id']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue is not None
            assert issue.state == 'open'
            assert issue.source_type == 'inspection'
            assert issue.fbo_id == '200000000000001'
            assert issue.fso_name == 'Test FSO'
            assert issue.manufacturer_fbo_id is None
            audits = db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).all()
            assert len(audits) == 1
            audit = audits[0]
            assert audit.from_state is None
            assert audit.to_state == 'open'
            assert audit.asserted_by == 'Test FSO'
    
    def test_create_sample_issue_non_packaged(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'sample',
            'fbo_id': '200000000000002',
            'fso_name': 'Test FSO 2',
            'detail_json': {
                'sampling_date': '2026-07-16',
                'sample_name': 'Test Sample',
                'price': '100',
                'sample_code': 'SAMP001'
            }
        }
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 201
        issue_id = response.get_json()['issue_id']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.source_type == 'sample'
            assert issue.manufacturer_fbo_id is None
            audits = db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).all()
            assert len(audits) == 1
    
    def test_create_sample_issue_packaged(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'sample',
            'fbo_id': '200000000000003',
            'fso_name': 'Test FSO 3',
            'manufacturer_fbo_id': '200000000000004',
            'detail_json': {
                'sampling_date': '2026-07-16',
                'sample_name': 'Test Packaged',
                'price': '200',
                'sample_code': 'SAMP002'
            }
        }
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 201
        issue_id = response.get_json()['issue_id']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.source_type == 'sample'
            assert issue.manufacturer_fbo_id == '200000000000004'


# ============================================================================
# TEST GROUP B: Creation - rejection cases
# ============================================================================

class TestCreationRejection:
    def test_fbo_id_lookup_failure(self, client, monkeypatch):
        def mock_lookup_fail(license_no):
            return None, "License/Registration number not found."
        import app.utils.lookup as lookup_module
        monkeypatch.setattr(lookup_module, 'lookup_fssai', mock_lookup_fail)
        import app.fbo_issue.routes as routes_module
        monkeypatch.setattr(routes_module, 'lookup_fssai', mock_lookup_fail)
        with client.application.app_context():
            initial_issue_count = db.session.query(FboIssue).count()
            initial_audit_count = db.session.query(FboIssueAudit).count()
        data = {
            'source_type': 'inspection',
            'fbo_id': '999999999999999',
            'fso_name': 'Test FSO',
            'detail_json': {'checklist': ['item1']}
        }
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 400
        assert 'Invalid fbo_id' in response.get_json()['error']
        with client.application.app_context():
            assert db.session.query(FboIssue).count() == initial_issue_count
            assert db.session.query(FboIssueAudit).count() == initial_audit_count
    
    def test_inspection_with_sample_fields(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000010',
            'fso_name': 'Test FSO',
            'detail_json': {'sampling_date': '2026-07-16', 'sample_name': 'Wrong', 'price': '100', 'sample_code': 'SAMP001'}
        }
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 400
        assert 'Missing required fields for inspection' in response.get_json()['error']
    
    def test_inspection_with_manufacturer_fbo_id(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000011',
            'fso_name': 'Test FSO',
            'manufacturer_fbo_id': '200000000000012',
            'detail_json': {'checklist': ['item1']}
        }
        response = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert response.status_code == 400
        assert 'manufacturer_fbo_id must be null for inspection' in response.get_json()['error']


# ============================================================================
# TEST GROUP C: State transitions - valid
# ============================================================================

class TestStateTransitionsValid:
    def test_full_workflow_inspection(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000020',
            'fso_name': 'Test Workflow',
            'detail_json': {'checklist': ['item1']}
        }
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'open'
            assert db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count() == 1
        t1 = client.post(f'/fbo-issue/{issue_id}/transition',
                         data=json.dumps({'to_state': 'permission_pending', 'asserted_by': 'User1'}),
                         content_type='application/json')
        assert t1.status_code == 200
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'permission_pending'
            assert db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count() == 2
            audits = db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).order_by(FboIssueAudit.id).all()
            assert audits[0].from_state is None
            assert audits[0].to_state == 'open'
            assert audits[1].from_state == 'open'
            assert audits[1].to_state == 'permission_pending'
        t2 = client.post(f'/fbo-issue/{issue_id}/transition',
                         data=json.dumps({'to_state': 'permission_granted', 'asserted_by': 'User2'}),
                         content_type='application/json')
        assert t2.status_code == 200
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'permission_granted'
            assert db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count() == 3
        t3 = client.post(f'/fbo-issue/{issue_id}/transition',
                         data=json.dumps({'to_state': 'closed', 'asserted_by': 'User3'}),
                         content_type='application/json')
        assert t3.status_code == 200
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'closed'
            assert db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count() == 4
            audits = db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).order_by(FboIssueAudit.id).all()
            states = [(a.from_state, a.to_state) for a in audits]
            assert states == [(None, 'open'), ('open', 'permission_pending'), ('permission_pending', 'permission_granted'), ('permission_granted', 'closed')]
    
    def test_open_to_dismissed_inspection(self, client, mock_lookup_fssai):
        data = {'source_type': 'inspection', 'fbo_id': '200000000000021', 'fso_name': 'Test Dismiss', 'detail_json': {'checklist': ['item1']}}
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        t = client.post(f'/fbo-issue/{issue_id}/transition',
                        data=json.dumps({'to_state': 'dismissed', 'asserted_by': 'User'}),
                        content_type='application/json')
        assert t.status_code == 200
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'dismissed'


# ============================================================================
# TEST GROUP D: State transitions - invalid/rejected
# ============================================================================

class TestStateTransitionsInvalid:
    def test_sample_open_to_dismissed_rejected(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'sample',
            'fbo_id': '200000000000030',
            'fso_name': 'Test Sample',
            'detail_json': {'sampling_date': '2026-07-16', 'sample_name': 'X', 'price': '100', 'sample_code': 'SAMP100'}
        }
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        t = client.post(f'/fbo-issue/{issue_id}/transition',
                        data=json.dumps({'to_state': 'dismissed', 'asserted_by': 'User'}),
                        content_type='application/json')
        assert t.status_code == 400
        assert 'Cannot transition sample source_type to dismissed' in t.get_json()['error']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'open'
    
    def test_out_of_sequence_transition(self, client, mock_lookup_fssai):
        data = {'source_type': 'inspection', 'fbo_id': '200000000000031', 'fso_name': 'Test OutOfSequence', 'detail_json': {'checklist': ['item1']}}
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        t = client.post(f'/fbo-issue/{issue_id}/transition',
                        data=json.dumps({'to_state': 'closed', 'asserted_by': 'User'}),
                        content_type='application/json')
        assert t.status_code == 400
        assert 'Invalid state transition' in t.get_json()['error']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            assert issue.state == 'open'
    
    def test_nonexistent_issue_id(self, client):
        t = client.post('/fbo-issue/99999/transition',
                        data=json.dumps({'to_state': 'permission_pending', 'asserted_by': 'User'}),
                        content_type='application/json')
        assert t.status_code == 404
        assert 'not found' in t.get_json()['error']


# ============================================================================
# TEST GROUP E: Atomicity check
# ============================================================================

class TestAtomicity:
    def test_atomicity_rollback_on_audit_failure(self, client, mock_lookup_fssai, monkeypatch):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000040',
            'fso_name': 'Test Atomicity',
            'detail_json': {'checklist': ['item1']}
        }
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        with client.application.app_context():
            issue = db.session.get(FboIssue, issue_id)
            initial_state = issue.state
            assert initial_state == 'open'
            initial_audit_count = db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count()
        add_call_count = [0]
        original_add = db.session.add
        def mock_add(obj):
            add_call_count[0] += 1
            if add_call_count[0] == 2:
                raise Exception("Simulated DB failure during audit insert")
            return original_add(obj)
        monkeypatch.setattr(db.session, 'add', mock_add)
        try:
            t = client.post(f'/fbo-issue/{issue_id}/transition',
                            data=json.dumps({'to_state': 'permission_pending', 'asserted_by': 'User'}),
                            content_type='application/json')
            assert t.status_code == 500
            with client.application.app_context():
                issue = db.session.get(FboIssue, issue_id)
                assert issue.state == initial_state
                assert db.session.query(FboIssueAudit).filter_by(issue_id=issue_id).count() == initial_audit_count
        finally:
            db.session.add = original_add


# ============================================================================
# TEST GROUP F: Read routes
# ============================================================================

class TestReadRoutes:
    def test_get_issue_with_audit_history(self, client, mock_lookup_fssai):
        data = {
            'source_type': 'inspection',
            'fbo_id': '200000000000050',
            'fso_name': 'Test Read',
            'fbo_name': 'Test FBO Read',
            'detail_json': {'checklist': ['item1', 'item2']}
        }
        resp = client.post('/fbo-issue/new', data=json.dumps(data), content_type='application/json')
        assert resp.status_code == 201
        issue_id = resp.get_json()['issue_id']
        client.post(f'/fbo-issue/{issue_id}/transition',
                    data=json.dumps({'to_state': 'permission_pending', 'asserted_by': 'User'}),
                    content_type='application/json')
        resp = client.get(f'/fbo-issue/{issue_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['id'] == issue_id
        assert data['source_type'] == 'inspection'
        assert data['state'] == 'permission_pending'
        assert data['fbo_name'] == 'Test FBO Read'
        assert data['detail'] == {'checklist': ['item1', 'item2']}
        assert 'audit_history' in data
        assert len(data['audit_history']) == 2
        assert data['audit_history'][0]['from_state'] is None
        assert data['audit_history'][0]['to_state'] == 'open'
        assert data['audit_history'][1]['from_state'] == 'open'
        assert data['audit_history'][1]['to_state'] == 'permission_pending'
    
    def test_list_by_fbo_id(self, client, mock_lookup_fssai):
        data1 = {'source_type': 'inspection', 'fbo_id': '200000000000060', 'fso_name': 'FSO A', 'detail_json': {'checklist': ['a']}}
        data2 = {'source_type': 'inspection', 'fbo_id': '200000000000061', 'fso_name': 'FSO B', 'detail_json': {'checklist': ['b']}}
        data3 = {'source_type': 'inspection', 'fbo_id': '200000000000060', 'fso_name': 'FSO C', 'detail_json': {'checklist': ['c']}}
        r1 = client.post('/fbo-issue/new', data=json.dumps(data1), content_type='application/json')
        r2 = client.post('/fbo-issue/new', data=json.dumps(data2), content_type='application/json')
        r3 = client.post('/fbo-issue/new', data=json.dumps(data3), content_type='application/json')
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 201
        resp = client.get('/fbo-issue/?fbo_id=200000000000060')
        assert resp.status_code == 200
        issues = resp.get_json()
        assert len(issues) == 2
        for issue in issues:
            assert issue['fbo_id'] == '200000000000060'
        resp = client.get('/fbo-issue/?fbo_id=200000000000061')
        assert resp.status_code == 200
        issues = resp.get_json()
        assert len(issues) == 1
        assert issues[0]['fbo_id'] == '200000000000061'
    
    def test_list_by_state(self, client, mock_lookup_fssai):
        data1 = {'source_type': 'inspection', 'fbo_id': '200000000000070', 'fso_name': 'FSO Open', 'detail_json': {'checklist': ['a']}}
        r1 = client.post('/fbo-issue/new', data=json.dumps(data1), content_type='application/json')
        issue1_id = r1.get_json()['issue_id']
        client.post(f'/fbo-issue/{issue1_id}/transition',
                    data=json.dumps({'to_state': 'permission_pending', 'asserted_by': 'User'}),
                    content_type='application/json')
        data2 = {'source_type': 'inspection', 'fbo_id': '200000000000071', 'fso_name': 'FSO Open2', 'detail_json': {'checklist': ['b']}}
        r2 = client.post('/fbo-issue/new', data=json.dumps(data2), content_type='application/json')
        assert r2.status_code == 201
        resp = client.get('/fbo-issue/?state=open')
        assert resp.status_code == 200
        issues = resp.get_json()
        assert len(issues) == 1
        assert issues[0]['state'] == 'open'
        resp = client.get('/fbo-issue/?state=permission_pending')
        assert resp.status_code == 200
        issues = resp.get_json()
        assert len(issues) == 1
        assert issues[0]['state'] == 'permission_pending'
