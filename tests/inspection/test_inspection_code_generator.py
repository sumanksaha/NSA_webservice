"""Unit tests for InspectionCodeGenerator.

These tests verify the deepening of the inspection module by testing:
- InspectionCodeGenerator class behavior
- Pure functions without Flask/DB dependencies
- Backward compatibility with existing API
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.inspection.code_generation import (
    InspectionCodeGenerator,
    calculate_compliance_deadline,
    generate_inspection_code,
)


class TestInspectionCodeGenerator:
    """Tests for the InspectionCodeGenerator class."""

    def test_generate_code_format(self):
        """Generated code follows INSP-YYYY-##### format."""
        generator = InspectionCodeGenerator()
        with patch("app.inspection.code_generation.db.session") as mock_session:
            mock_seq = MagicMock()
            mock_seq.last_value = 42
            mock_session.get.return_value = mock_seq

            code = generator.generate_code()

        assert code.startswith("INSP-")
        assert len(code) == 15  # INSP-YYYY-##### (15 chars total)
        year = datetime.now(UTC).year
        assert str(year) in code

    def test_generate_code_increments_sequence(self):
        """Each call increments the sequence value."""
        generator = InspectionCodeGenerator()
        with patch("app.inspection.code_generation.db.session") as mock_session:
            mock_seq = MagicMock()
            mock_seq.last_value = 0
            mock_session.get.return_value = mock_seq

            generator.generate_code()
            generator.generate_code()

        assert mock_seq.last_value == 2

    def test_calculate_compliance_deadline_from_datetime(self):
        """Deadline is 30 days from datetime input."""
        generator = InspectionCodeGenerator()
        inspection_date = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
        deadline = generator.calculate_compliance_deadline(inspection_date)

        expected = datetime(2026, 2, 14, 10, 30, tzinfo=UTC)
        assert deadline == expected

    def test_calculate_compliance_deadline_from_date(self):
        """Deadline is 30 days from date input."""
        from datetime import date

        generator = InspectionCodeGenerator()
        inspection_date = date(2026, 1, 15)
        deadline = generator.calculate_compliance_deadline(inspection_date)

        expected = datetime(2026, 2, 14, 0, 0)
        assert deadline == expected

    def test_calculate_compliance_deadline_from_iso_string(self):
        """Deadline is 30 days from ISO format string."""
        generator = InspectionCodeGenerator()
        deadline = generator.calculate_compliance_deadline("2026-01-15")

        expected = datetime(2026, 2, 14, 0, 0)
        assert deadline == expected

    def test_calculate_compliance_deadline_invalid_input(self):
        """Returns None for invalid input."""
        generator = InspectionCodeGenerator()
        assert generator.calculate_compliance_deadline("not-a-date") is None
        assert generator.calculate_compliance_deadline(None) is None

    def test_calculate_compliance_deadline_empty_string(self):
        """Returns None for empty string."""
        generator = InspectionCodeGenerator()
        assert generator.calculate_compliance_deadline("") is None

    def test_calculate_compliance_deadline_year_boundary(self):
        """Deadline wraps across year boundary correctly."""
        generator = InspectionCodeGenerator()
        inspection_date = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
        deadline = generator.calculate_compliance_deadline(inspection_date)

        expected = datetime(2027, 1, 30, 23, 59, tzinfo=UTC)
        assert deadline == expected


class TestBackwardCompatibleWrappers:
    """Tests for backward-compatible wrapper functions."""

    def test_generate_inspection_code_wrapper(self):
        """Wrapper calls the class method."""
        with patch("app.inspection.code_generation.InspectionCodeGenerator.generate_code") as mock:
            mock.return_value = "INSP-2026-00001"
            result = generate_inspection_code()
            assert result == "INSP-2026-00001"
            mock.assert_called_once()

    def test_calculate_compliance_deadline_wrapper(self):
        """Wrapper calls the class method."""
        with patch("app.inspection.code_generation.InspectionCodeGenerator.calculate_compliance_deadline") as mock:
            mock.return_value = datetime(2026, 2, 14)
            result = calculate_compliance_deadline("2026-01-15")
            assert result == datetime(2026, 2, 14)
            mock.assert_called_once_with("2026-01-15")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
