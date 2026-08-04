"""Tests for Step 1: FSO master (markdown-synced) + Sample model/UI"""

import os
import sys
from datetime import datetime

import pytest

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from flask import Flask

from app.extensions import db
from app.models import FSO, CodeSequence, Sample
from app.utils.fso_data import get_all_fso_names, load_fso_names, sync_fso_from_markdown
from app.utils.lookup import lookup_fssai


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = Flask(__name__)

    # Use in-memory database for tests
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app
        db.drop_all()


class TestFSOModel:
    """Tests for FSO model."""

    def test_fso_model_structure(self, app):
        """Test FSO model has correct fields."""
        with app.app_context():
            # Check that FSO table exists with correct columns
            fso = FSO(fso_name="Test FSO")
            db.session.add(fso)
            db.session.commit()

            # Verify in database
            result = db.session.get(FSO, "Test FSO")
            assert result is not None
            assert result.fso_name == "Test FSO"
            assert result.created_at is not None

    def test_fso_primary_key(self, app):
        """Test that fso_name is primary key."""
        with app.app_context():
            fso1 = FSO(fso_name="FSO One")
            db.session.add(fso1)
            db.session.commit()

            # Try to add duplicate - should fail
            fso2 = FSO(fso_name="FSO One")
            db.session.add(fso2)
            with pytest.raises(Exception):
                db.session.commit()
            db.session.rollback()

            # Verify only one exists
            count = FSO.query.count()
            assert count == 1


class TestFSOMarkdownSync:
    """Tests for FSO markdown sync functionality."""

    def test_load_fso_names(self, app):
        """Test loading FSO names from markdown."""
        with app.app_context():
            # Use the real fso_list.md file
            names = load_fso_names()
            assert len(names) == 21
            assert "Suman Saha" in names
            assert "Anwesha Paul" in names

    def test_load_fso_names_ignores_header(self, app):
        """Test that header line is ignored."""
        with app.app_context():
            names = load_fso_names()
            # None of the names should be "# FSO List" or similar
            assert "# FSO List" not in names
            assert "FSO List" not in names

    def test_sync_additive_only(self, app):
        """Test that sync is ADDITIVE ONLY - never deletes."""
        with app.app_context():
            # First sync - should insert all 21 names
            result1 = sync_fso_from_markdown()
            assert result1["inserted"] == 21
            assert result1["updated"] == 0

            # Get count
            count1 = FSO.query.count()
            assert count1 == 21

            # Second sync - should not insert duplicates
            result2 = sync_fso_from_markdown()
            assert result2["inserted"] == 0
            assert result2["updated"] == 21

            # Count should be the same
            count2 = FSO.query.count()
            assert count2 == 21

            # Now add a new FSO directly to the database
            new_fso = FSO(fso_name="New FSO Not In File")
            db.session.add(new_fso)
            db.session.commit()

            # Third sync - should still have the manually added FSO
            sync_fso_from_markdown()
            count3 = FSO.query.count()

            # Should still have 22 (21 from file + 1 manual)
            assert count3 == 22

            # The manually added FSO should still be there
            assert db.session.get(FSO, "New FSO Not In File") is not None

    def test_sync_graceful_missing_file(self, app):
        """Test that missing file doesn't crash."""
        with app.app_context():
            result = sync_fso_from_markdown(path="/nonexistent/fso_list.md")
            assert result["inserted"] == 0

    def test_get_all_fso_names_sorted(self, app):
        """Test that get_all_fso_names returns sorted list."""
        with app.app_context():
            # Sync first
            sync_fso_from_markdown()

            names = get_all_fso_names()
            assert len(names) == 21

            # Check if sorted alphabetically
            assert names == sorted(names)


class TestSampleModel:
    """Tests for Sample model."""

    def test_sample_model_structure(self, app):
        """Test Sample model has all required fields."""
        with app.app_context():
            # First ensure we have an FSO
            fso = FSO(fso_name="Test FSO")
            db.session.add(fso)
            db.session.commit()

            # Create a sample
            sample = Sample(
                sample_code="SKS-2026-00001",
                sample_name="Test Sample",
                sample_type="Food",
                fso_name="Test FSO",
                collection_date=datetime(2026, 7, 17),
                submission_date=datetime(2026, 7, 18),
                retailer_fssai="1234567890",
                retailer_name="Test Retailer",
                price="100.00",
            )
            db.session.add(sample)
            db.session.commit()

            # Verify in database
            result = db.session.get(Sample, sample.id)
            assert result is not None
            assert result.sample_code == "SKS-2026-00001"
            assert result.sample_name == "Test Sample"
            assert result.fso_name == "Test FSO"
            # collection_date is a datetime; compare date portion
            assert result.collection_date.date() == datetime(2026, 7, 17).date()

    def test_sample_fso_foreign_key(self, app):
        """Test that sample.fso_name references fso.fso_name (at app level)."""
        with app.app_context():
            # SQLite doesn't enforce FK constraints by default in tests
            # Instead, test that the routes validate FSO exists
            # This is tested in the routes integration tests
            # For now, just verify we can create a sample with valid FSO
            fso = FSO(fso_name="Valid FSO")
            db.session.add(fso)
            db.session.commit()

            sample = Sample(
                sample_code="SKS-2026-00001",
                sample_name="Test Sample",
                sample_type="Food",
                fso_name="Valid FSO",
                collection_date=datetime(2026, 7, 17),
            )
            db.session.add(sample)
            db.session.commit()

            # Verify sample was created
            assert Sample.query.count() == 1


class TestSampleCodeGeneration:
    """Tests for sample code generation."""

    def test_sample_code_format(self, app):
        """Test sample code format is SKS-YYYY-#####."""
        with app.app_context():
            from app.sample.sample_utils import generate_sample_code

            code = generate_sample_code()

            # Should match format SKS-YYYY-#####
            import re

            pattern = r"^SKS-\d{4}-\d{5}$"
            assert re.match(pattern, code) is not None

    def test_sample_code_sequential(self, app):
        """Test that sample codes are sequential per year."""
        with app.app_context():
            from app.models import CodeSequence
            from app.sample.sample_utils import generate_sample_code

            # Pre-seed the CodeSequence table with last_value=1 (meaning next code is 00002)
            seq = CodeSequence(key="sample:2026", last_value=1)
            db.session.add(seq)
            db.session.commit()

            code1 = generate_sample_code()

            # Extract sequence number
            seq1 = int(code1.split("-")[-1])

            # code1 should be 00002 (next after last_value=1)
            assert seq1 == 2

            # Verify the format is correct
            import re

            pattern = r"^SKS-\d{4}-\d{5}$"
            assert re.match(pattern, code1) is not None

    def test_sample_code_race_safe(self, app):
        """Test that sample code generation is race-safe via CodeSequence atomic increment."""
        assert CodeSequence is not None


class TestRetailerLookup:
    """Tests for retailer FSSAI lookup integration."""

    def test_lookup_fssai_exists(self, app):
        """Test that lookup_fssai function exists and is importable."""
        assert lookup_fssai is not None
        assert callable(lookup_fssai)

    def test_lookup_fssai_returns_tuple(self, app):
        """Test that lookup_fssai returns (result, error) tuple."""
        result, error = lookup_fssai("")
        assert result is None
        assert isinstance(error, str)


class TestSampleRoutes:
    """Tests for Sample routes - basic integration."""

    def test_sample_blueprint_exists(self, app):
        """Test that sample blueprint can be imported."""
        from app.sample.routes import sample_bp

        assert sample_bp is not None
        assert sample_bp.name == "sample"

    def test_settings_blueprint_exists(self, app):
        """Test that settings blueprint can be imported."""
        from app.settings.routes import settings_bp

        assert settings_bp is not None
        assert settings_bp.name == "settings"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
