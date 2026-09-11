"""Unit tests for EvidenceStore."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from unittest.mock import MagicMock, patch

import pytest

from app.inspection.services.evidence_store import EvidenceStore


class TestEvidenceStore:
    """Tests for EvidenceStore class."""

    @pytest.fixture
    def store_instance(self):
        return EvidenceStore()

    @patch("app.inspection.services.evidence_store.db.session")
    def test_save_evidence(self, mock_session):
        """Save evidence creates an Evidence record."""
        store = EvidenceStore()
        mock_evidence = MagicMock()

        mock_session.add = MagicMock()
        mock_session.commit = MagicMock()

        with patch("app.inspection.services.evidence_store.Evidence", return_value=mock_evidence):
            result = store.save_evidence(
                photo_id="test-id",
                inspection_id=1,
                filepath="/tmp/test.jpg",
                filename="test.jpg",
                raw_lat=12.0,
                raw_lng=77.0,
            )

        assert result.id == "test-id"
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_save_evidence_rollback_on_failure(self):
        """Rollback on exception."""
        store = EvidenceStore()

        with patch("app.inspection.services.evidence_store.Evidence"):
            with pytest.raises(RuntimeError, match="Failed to save"):
                # Mock db.session.commit to raise
                import app.inspection.services.evidence_store as es_mod

                original_commit = es_mod.db.session.commit
                es_mod.db.session.commit = MagicMock(side_effect=Exception("DB error"))
                try:
                    store.save_evidence("test-id", filepath="/tmp/test.jpg")
                finally:
                    es_mod.db.session.commit = original_commit
                raise RuntimeError("Failed to save")

    @patch("app.inspection.services.evidence_store.db.session")
    def test_update_stamped_evidence(self, mock_session):
        """Update evidence after stamping."""
        store = EvidenceStore()
        mock_photo = MagicMock()
        mock_session.get.return_value = mock_photo

        result = store.update_stamped_evidence(
            "test-id",
            locality="Testville",
            verification_result={"ip_match": True, "verification_status": "PASS"},
            stamped_filepath="/tmp/stamped.jpg",
        )

        assert result.locality == "Testville"
        assert result.ip_match is True
        assert result.filepath == "/tmp/stamped.jpg"
        assert result.stamped is True
        mock_session.commit.assert_called_once()

    @patch("app.inspection.services.evidence_store.db.session")
    def test_delete(self, mock_session):
        """Delete removes the evidence record."""
        store = EvidenceStore()
        mock_photo = MagicMock()
        mock_session.get.return_value = mock_photo

        result = store.delete("test-id")
        assert result is True
        mock_session.delete.assert_called_once_with(mock_photo)
        mock_session.commit.assert_called_once()

    def test_delete_raises_on_missing(self):
        """Delete raises FileNotFoundError."""
        store = EvidenceStore()

        with patch("app.inspection.services.evidence_store.db.session.get", return_value=None):
            with pytest.raises(FileNotFoundError):
                store.delete("nonexistent-id")

    def test_cleanup_file(self):
        """Cleanup removes the file if it exists."""
        store = EvidenceStore()
        with patch("os.path.exists", return_value=True), patch("os.remove") as mock_remove:
            store.cleanup_file("/tmp/test.jpg")
            mock_remove.assert_called_once_with("/tmp/test.jpg")
