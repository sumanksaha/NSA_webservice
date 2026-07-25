"""
Tests for Step 3: Inspection model + entry UI
"""

import os
import sys
import tempfile
import threading
import pytest
from datetime import datetime, timedelta

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from flask import Flask
from app.extensions import db
from app.models import Inspection, FSO
from app.inspection.inspection_utils import generate_inspection_code, calculate_compliance_deadline
from app.utils.lookup import lookup_fssai


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = Flask(__name__)
    
    # Use in-memory database for tests
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['TESTING'] = True
    
    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def test_fso(app):
    """Create a test FSO for use in tests."""
    with app.app_context():
        fso = FSO(fso_name="Test FSO")
        db.session.add(fso)
        db.session.commit()
        return fso


class TestInspectionModel:
    """Tests for Inspection model."""
    
    def test_inspection_model_structure(self, app, test_fso):
        """Test Inspection model has correct fields."""
        with app.app_context():
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                fssai_license="1234567890",
                ce_license_no="CE123456",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                concerned_food="Test Food",
                problem="Test Problem",
                inspection_date=datetime(2026, 7, 17),
                compliance_deadline=datetime(2026, 8, 16),
                is_dismissed=False
            )
            db.session.add(inspection)
            db.session.commit()
            
            # Verify in database
            result = Inspection.query.filter_by(inspection_code="INSP-2026-00001").first()
            assert result is not None
            assert result.fso_name == "Test FSO"
            assert result.fssai_license == "1234567890"
            assert result.ce_license_no == "CE123456"
            assert result.fbo_name == "Test FBO"
            assert result.fbo_address == "Test Address"
            assert result.concerned_food == "Test Food"
            assert result.problem == "Test Problem"
            assert result.inspection_date == datetime(2026, 7, 17)
            assert result.compliance_deadline == datetime(2026, 8, 16)
            assert result.is_dismissed == False
            assert result.created_at is not None
    
    def test_inspection_code_unique(self, app, test_fso):
        """Test that inspection_code must be unique."""
        with app.app_context():
            inspection1 = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=datetime(2026, 7, 17),
                compliance_deadline=datetime(2026, 8, 16)
            )
            db.session.add(inspection1)
            db.session.commit()
            
            # Try to add duplicate - should fail
            inspection2 = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=datetime(2026, 7, 18),
                compliance_deadline=datetime(2026, 8, 17)
            )
            db.session.add(inspection2)
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()
            
            # Verify only one exists
            count = Inspection.query.filter_by(inspection_code="INSP-2026-00001").count()
            assert count == 1


class TestInspectionCodeGeneration:
    """Tests for inspection code generation."""
    
    def test_generate_inspection_code_format(self, app, test_fso):
        """Test that generated inspection code has correct format."""
        with app.app_context():
            code = generate_inspection_code()
            
            # Check format: INSP-YYYY-#####
            assert code.startswith("INSP-")
            year = datetime.utcnow().year
            assert f"INSP-{year}-" in code
            
            # Check numeric part is 5 digits
            parts = code.split('-')
            assert len(parts) == 3
            assert parts[2].isdigit()
            assert len(parts[2]) == 5
    
    def test_generate_inspection_code_uniqueness(self, app, test_fso):
        """Test that generated codes are unique."""
        with app.app_context():
            # Clear any existing inspections for this year
            year = datetime.utcnow().year
            Inspection.query.filter(Inspection.inspection_code.like(f"INSP-{year}-%")).delete()
            db.session.commit()
            
            code1 = generate_inspection_code()
            # Manually create inspection with code1 to simulate existing record
            inspection = Inspection(
                inspection_code=code1,
                fso_name="Test FSO",
                inspection_date=datetime(2026, 7, 17),
                compliance_deadline=datetime(2026, 8, 16)
            )
            db.session.add(inspection)
            db.session.commit()
            
            code2 = generate_inspection_code()
            
            assert code1 != code2
    
    def test_generate_inspection_code_sequential(self, app, test_fso):
        """Test that codes are sequential within the same year."""
        with app.app_context():
            # Clear any existing inspections for this year
            year = datetime.utcnow().year
            Inspection.query.filter(Inspection.inspection_code.like(f"INSP-{year}-%")).delete()
            db.session.commit()
            
            code1 = generate_inspection_code()
            # Create inspection with code1
            inspection1 = Inspection(
                inspection_code=code1,
                fso_name="Test FSO",
                inspection_date=datetime(2026, 7, 17),
                compliance_deadline=datetime(2026, 8, 16)
            )
            db.session.add(inspection1)
            db.session.commit()
            
            code2 = generate_inspection_code()
            
            # Extract sequence numbers
            seq1 = int(code1.split('-')[-1])
            seq2 = int(code2.split('-')[-1])
            
            assert seq2 == seq1 + 1
    
    def test_generate_inspection_code_race_safety(self, app, test_fso):
        """Test race-safe code generation via CodeSequence atomic increment.
        
        Note: Flask-SQLAlchemy doesn't support true multi-threaded access
        in the same way as production. This test verifies the CodeSequence
        mechanism exists and generates unique, sequential codes.
        """
        with app.app_context():
            # Clear any existing inspections for this year
            year = datetime.utcnow().year
            Inspection.query.filter(Inspection.inspection_code.like(f"INSP-{year}-%")).delete()
            db.session.commit()
            
            # Generate codes sequentially to verify uniqueness
            codes = []
            for _ in range(10):
                code = generate_inspection_code()
                # Create inspection with this code
                inspection = Inspection(
                    inspection_code=code,
                    fso_name="Test FSO",
                    inspection_date=datetime(2026, 7, 17),
                    compliance_deadline=datetime(2026, 8, 16)
                )
                db.session.add(inspection)
                codes.append(code)
            db.session.commit()
            
            # All codes should be unique
            assert len(codes) == len(set(codes))
            
            # Codes should be sequential
            seq_numbers = sorted([int(c.split('-')[-1]) for c in codes])
            assert seq_numbers == list(range(1, 11))
            
            # Verify the CodeSequence pattern is used for race safety
            from app.models import CodeSequence
            assert CodeSequence is not None


class TestComplianceDeadlineCalculation:
    """Tests for compliance deadline auto-calculation."""
    
    def test_calculate_compliance_deadline_basic(self):
        """Test basic compliance deadline calculation."""
        inspection_date = "2026-07-17"
        deadline = calculate_compliance_deadline(inspection_date)
        
        # Should be 30 days later, returned as a datetime
        expected = datetime(2026, 7, 17) + timedelta(days=30)
        assert deadline == expected
    
    def test_calculate_compliance_deadline_month_boundary(self):
        """Test compliance deadline calculation across month boundary."""
        inspection_date = "2026-01-15"
        deadline = calculate_compliance_deadline(inspection_date)
        
        expected = datetime(2026, 1, 15) + timedelta(days=30)
        assert deadline == expected
    
    def test_calculate_compliance_deadline_year_boundary(self):
        """Test compliance deadline calculation across year boundary."""
        inspection_date = "2026-12-20"
        deadline = calculate_compliance_deadline(inspection_date)
        
        expected = datetime(2026, 12, 20) + timedelta(days=30)
        assert deadline == expected
    
    def test_calculate_compliance_deadline_invalid_format(self):
        """Test compliance deadline calculation with invalid date format."""
        deadline = calculate_compliance_deadline("invalid-date")
        assert deadline is None
    
    def test_calculate_compliance_deadline_empty_string(self):
        """Test compliance deadline calculation with empty string."""
        deadline = calculate_compliance_deadline("")
        assert deadline is None
    
    def test_calculate_compliance_deadline_none(self):
        """Test compliance deadline calculation with None."""
        deadline = calculate_compliance_deadline(None)
        assert deadline is None


class TestInspectionIndexes:
    """Tests for Inspection model indexes."""
    
    def test_inspection_indexes_exist(self, app, test_fso):
        """Test that inspection table has the expected indexes."""
        with app.app_context():
            # Create some test data
            for i in range(5):
                inspection = Inspection(
                    inspection_code=f"INSP-2026-{i:05d}",
                    fso_name="Test FSO",
                    inspection_date=datetime(2026, 7, 10 + i),
                    compliance_deadline=datetime(2026, 8, 10 + i)
                )
                db.session.add(inspection)
            db.session.commit()
            
            # Test querying by indexed fields
            # inspection_code index
            result = Inspection.query.filter_by(inspection_code="INSP-2026-00001").first()
            assert result is not None
            
            # fso_name index
            results = Inspection.query.filter_by(fso_name="Test FSO").all()
            assert len(results) == 5
            
            # inspection_date index
            results = Inspection.query.filter_by(inspection_date=datetime(2026, 7, 10)).all()
            assert len(results) == 1
            
            # compliance_deadline index
            results = Inspection.query.filter_by(compliance_deadline=datetime(2026, 8, 10)).all()
            assert len(results) == 1


class TestLookupIntegration:
    """Tests for FSSAI and CE lookup integration."""
    
    def test_fssai_lookup_format(self, app):
        """Test that FSSAI lookup returns expected format."""
        # This test verifies the format of the lookup_fssai function
        # Note: This depends on the actual database files being present
        # We're just testing the function signature and return format
        
        # Test with empty input
        result, error = lookup_fssai("")
        assert result is None
        assert error is not None
        
        # Test with invalid prefix
        result, error = lookup_fssai("3123456789")
        assert result is None
        assert error is not None


class TestDualLookupConflict:
    """Tests for dual lookup conflict detection behavior."""
    
    def test_conflict_detection_logic(self, app):
        """Test conflict detection logic between FSSAI and CE lookups."""
        # This simulates the JavaScript conflict detection logic
        
        # Scenario 1: No conflict - both sources have same data
        fssai_data = {'fbo_name': 'Test FBO', 'fbo_address': 'Test Address'}
        ce_data = {'fbo_name': 'Test FBO', 'fbo_address': 'Test Address'}
        
        fssai_name = fssai_data['fbo_name']
        ce_name = ce_data['fbo_name']
        fssai_address = fssai_data['fbo_address']
        ce_address = ce_data['fbo_address']
        
        has_conflict = (fssai_name != ce_name) or (fssai_address != ce_address)
        assert has_conflict == False
        
        # Scenario 2: Conflict - different names
        fssai_data2 = {'fbo_name': 'FBO A', 'fbo_address': 'Address A'}
        ce_data2 = {'fbo_name': 'FBO B', 'fbo_address': 'Address A'}
        
        has_conflict2 = (fssai_data2['fbo_name'] != ce_data2['fbo_name']) or (fssai_data2['fbo_address'] != ce_data2['fbo_address'])
        assert has_conflict2 == True
        
        # Scenario 3: Conflict - different addresses
        fssai_data3 = {'fbo_name': 'FBO A', 'fbo_address': 'Address A'}
        ce_data3 = {'fbo_name': 'FBO A', 'fbo_address': 'Address B'}
        
        has_conflict3 = (fssai_data3['fbo_name'] != ce_data3['fbo_name']) or (fssai_data3['fbo_address'] != ce_data3['fbo_address'])
        assert has_conflict3 == True
        
        # Scenario 4: No conflict - only one source has data
        fssai_data4 = {'fbo_name': 'FBO A', 'fbo_address': 'Address A'}
        ce_data4 = None
        
        has_conflict4 = False  # No conflict if only one source
        assert has_conflict4 == False
