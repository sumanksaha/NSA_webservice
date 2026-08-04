"""Tests for the DocumentSaveCoordinator (D2 deepening task).

Verifies that the coordinator correctly orchestrates content persistence,
version snapshotting (force vs dedup), and audit logging.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.extensions import db
from app.models import Adjudication, CaseFile, FSO
from app.services.document_lifecycle import DocumentSaveCoordinator, SaveResult


@pytest.fixture
def test_app():
    """Create a minimal Flask app with an in-memory SQLite database."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["DISABLE_PDF_GENERATION"] = "1"
    with app.app_context():
        db.drop_all()
        db.create_all()
        with app.test_request_context():
            yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def case_file(test_app):
    """Create a CaseFile for testing."""
    fso = FSO(fso_name="Test FSO")
    db.session.add(fso)
    db.session.commit()

    cf = CaseFile(
        case_number="CF-001",
        food_safety_officer_name="Test FSO",
        authorization_date=datetime(2026, 1, 1),
        inspection_date=datetime(2026, 1, 1),
        inspection_time="10:00",
        manufacturer_fssai="MFG123",
        manufacturer_name="Test Mfg",
        manufacturer_fbo_name="Test MFG FBO",
        manufacturer_address="123 Mfg St",
        retailer_fssai="RET456",
        retailer_name="Test Retailer",
        retailer_fbo_name="Test Retailer FBO",
        retailer_address="456 Retail St",
        product_name="Test Product",
        batch_no="BATCH001",
        sample_quantity="1000g",
        packet_count=4,
        mfg_date=datetime(2026, 6, 1),
        expiry_date=datetime(2026, 8, 1),
        sample_code="SMP001",
        sample_submission_date=datetime(2026, 7, 2),
        Lab_Registration_No="WB/FOOD/2025/001",
        do_receipt_date=datetime(2026, 7, 4),
        analyst_report_no="AR-001",
        analyst_report_date=datetime(2026, 7, 5),
        directive_letter_no="DL-001",
        directive_letter_date=datetime(2026, 7, 6),
        retailer_report_receive_date=datetime(2026, 7, 7),
        manufacturer_report_receive_date=datetime(2026, 7, 8),
    )
    db.session.add(cf)
    db.session.commit()
    return cf


@pytest.fixture
def adjudication(test_app):
    """Create an Adjudication for testing."""
    adj = Adjudication(
        case_number="ADJ-001",
        food_safety_officer="Test FSO",
        non_license="no",
        pre_authorization="no",
        complaint_lodged="no",
        fbo_owner="Test Owner",
        fbo_name="Test FBO",
        fbo_address="123 FBO St",
        fssai_license="FSSAI123",
        concerned_food="Test Food",
        problem="Contamination",
        First_inspection_date=datetime(2026, 1, 1),
        compliance_deadline=datetime(2026, 2, 1),
        inspection_date=datetime(2026, 1, 15),
        authorization_date=None,
    )
    db.session.add(adj)
    db.session.commit()
    return adj


class TestDocumentSaveCoordinator:
    def test_save_success_case_file(self, test_app, case_file):
        """Saving a case_file document returns a successful SaveResult."""
        coordinator = DocumentSaveCoordinator()
        result = coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test content</p>",
            delta_content=None,
            force_snapshot=True,
        )
        assert isinstance(result, SaveResult)
        assert result.success is True
        assert result.timestamp is not None
        assert result.version_number is not None
        assert result.content_hash is not None

    def test_save_success_adjudication(self, test_app, adjudication):
        """Saving an adjudication document works with case_type='adjudication'."""
        coordinator = DocumentSaveCoordinator()
        result = coordinator.save(
            case_id=adjudication.id,
            case_type="adjudication",
            doc_type="permission",
            html_content="<p>Test content</p>",
            delta_content=None,
            force_snapshot=True,
        )
        assert result.success is True
        assert result.version_number is not None

    def test_save_with_delta(self, test_app, case_file):
        """Delta content is accepted and persisted."""
        coordinator = DocumentSaveCoordinator()
        delta = {"ops": [{"insert": "Hello"}]}
        result = coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Hello</p>",
            delta_content=delta,
            force_snapshot=True,
        )
        assert result.success is True
        assert result.version_number is not None

    @patch("app.services.document_lifecycle.VersionService")
    def test_force_snapshot_calls_create_version(self, mock_vs_cls, test_app, case_file):
        """force_snapshot=True calls VersionService.create_version."""
        mock_service = MagicMock()
        mock_vs_cls.return_value = mock_service
        mock_version = MagicMock()
        mock_version.version_number = 1
        mock_version.content_hash = "abc123"
        mock_service.create_version.return_value = mock_version

        coordinator = DocumentSaveCoordinator()
        coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test</p>",
            delta_content=None,
            force_snapshot=True,
        )

        mock_service.create_version.assert_called_once()
        call_kwargs = mock_service.create_version.call_args.kwargs
        assert call_kwargs["case_id"] == case_file.id
        assert call_kwargs["adjudication_id"] is None
        assert call_kwargs["doc_type"] == "petition"

    @patch("app.services.document_lifecycle.VersionService")
    def test_autosave_calls_create_version_if_changed(self, mock_vs_cls, test_app, case_file):
        """force_snapshot=False calls VersionService.create_version_if_changed."""
        mock_service = MagicMock()
        mock_vs_cls.return_value = mock_service
        mock_version = MagicMock()
        mock_version.version_number = 1
        mock_version.content_hash = "abc123"
        mock_service.create_version_if_changed.return_value = mock_version

        coordinator = DocumentSaveCoordinator()
        coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="permission",
            html_content="<p>Auto-saved</p>",
            delta_content=None,
            force_snapshot=False,
        )

        mock_service.create_version_if_changed.assert_called_once()
        call_kwargs = mock_service.create_version_if_changed.call_args.kwargs
        assert call_kwargs["case_id"] == case_file.id
        assert call_kwargs["adjudication_id"] is None

    @patch("app.services.document_lifecycle.VersionService")
    def test_snapshot_failure_does_not_block_save(self, mock_vs_cls, test_app, case_file):
        """VersionService failure is swallowed; save still succeeds."""
        mock_service = MagicMock()
        mock_vs_cls.return_value = mock_service
        mock_service.create_version.side_effect = RuntimeError("VersionService exploded")

        coordinator = DocumentSaveCoordinator()
        result = coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test</p>",
            delta_content=None,
            force_snapshot=True,
        )

        assert result.success is True
        assert result.timestamp is not None
        assert result.version_number is None
        assert result.content_hash is None

    @patch("app.services.document_lifecycle.log_audit")
    def test_audit_log_called_on_save(self, mock_log_audit, test_app, case_file):
        """Audit log is called with the correct action on explicit save."""
        coordinator = DocumentSaveCoordinator()
        coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test</p>",
            delta_content=None,
            force_snapshot=True,
        )

        mock_log_audit.assert_called_once()
        call_kwargs = mock_log_audit.call_args.kwargs
        assert call_kwargs["entity_type"] == "case_file"
        assert call_kwargs["entity_id"] == str(case_file.id)
        assert "DOCUMENT_EDITED" in call_kwargs["action"]

    @patch("app.services.document_lifecycle.log_audit")
    def test_audit_log_called_on_autosave(self, mock_log_audit, test_app, case_file):
        """Audit log is called with the correct action on autosave."""
        coordinator = DocumentSaveCoordinator()
        coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="permission",
            html_content="<p>Auto</p>",
            delta_content=None,
            force_snapshot=False,
        )

        call_kwargs = mock_log_audit.call_args.kwargs
        assert "DOCUMENT_AUTOSAVED" in call_kwargs["action"]

    @patch("app.services.document_lifecycle.log_audit")
    def test_audit_failure_swallowed(self, mock_log_audit, test_app, case_file):
        """Audit log failure does not block save."""
        mock_log_audit.side_effect = RuntimeError("Audit exploded")

        coordinator = DocumentSaveCoordinator()
        result = coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test</p>",
            delta_content=None,
            force_snapshot=True,
        )

        assert result.success is True

    def test_save_result_dataclass_fields(self, test_app, case_file):
        """SaveResult has all expected fields."""
        coordinator = DocumentSaveCoordinator()
        result = coordinator.save(
            case_id=case_file.id,
            case_type="case_file",
            doc_type="petition",
            html_content="<p>Test</p>",
            delta_content=None,
            force_snapshot=True,
        )
        assert isinstance(result, SaveResult)
        assert hasattr(result, "timestamp")
        assert hasattr(result, "version_number")
        assert hasattr(result, "content_hash")
        assert hasattr(result, "success")
