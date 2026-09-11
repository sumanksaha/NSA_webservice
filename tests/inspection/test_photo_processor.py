"""Unit tests for PhotoProcessor."""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.inspection.services.photo_processor import PhotoProcessor, ProcessedPhoto


class TestPhotoProcessor:
    """Tests for PhotoProcessor class."""

    @pytest.fixture
    def processor(self):
        return PhotoProcessor()

    def test_process_creates_processed_photo(self, processor):
        """Process returns a ProcessedPhoto with expected fields."""
        # Create a simple test image in memory
        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Mock the save method on the file-like object
        mock_file = MagicMock()
        mock_file.filename = "test.jpg"
        mock_file.read = lambda: img_bytes.getvalue()

        with patch("app.inspection.services.photo_processor.Image.open", return_value=img):
            with patch("app.inspection.services.photo_processor.current_app") as mock_app:
                mock_app.instance_path = "/tmp"
                form_data = {
                    "lat": "12.9716",
                    "lng": "77.5946",
                    "accuracy": "10.0",
                    "captured_at": "2026-01-01T12:00:00",
                }
                with patch("app.inspection.services.photo_processor.uuid.uuid4", return_value="test-uuid"):
                    with patch("app.inspection.services.photo_processor.secure_filename", return_value="test.jpg"):
                        with patch(
                            "app.inspection.services.photo_processor.mimetypes.guess_type",
                            return_value=("image/jpeg", None),
                        ):
                            result = processor.process(mock_file, form_data)

        assert isinstance(result, ProcessedPhoto)
        assert result.raw_lat == 12.9716
        assert result.raw_lng == 77.5946
        assert result.accuracy == 10.0
        assert result.filename == "test.jpg"

    def test_validate_file_invalid_extension(self, processor):
        """Invalid extension raises ValueError."""
        mock_file = MagicMock()
        mock_file.filename = "test.txt"
        with pytest.raises(ValueError):
            processor._validate_file(mock_file)

    def test_validate_file_valid(self, processor):
        """Valid file passes validation."""
        mock_file = MagicMock()
        mock_file.filename = "test.jpg"
        with patch("app.inspection.services.photo_processor.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img.verify.return_value = None
            mock_open.return_value = mock_img
            processor._validate_file(mock_file)
            mock_img.verify.assert_called_once()

    def test_extract_exif_gps(self, processor):
        """EXIF GPS extraction works."""
        mock_file = MagicMock()
        mock_img = MagicMock()
        mock_exif = {
            34853: {  # GPSInfo
                2: (12, 30, 0),  # GPSLatitude
                4: (77, 35, 0),  # GPSLongitude
            }
        }
        mock_img.getexif.return_value = mock_exif
        with patch("app.inspection.services.photo_processor.Image.open", return_value=mock_img):
            result = processor._extract_exif_gps(mock_file)
        assert result[0] is not None  # lat
        assert result[1] is not None  # lng

    def test_pick_coord_uses_form_value(self, processor):
        """Form value takes precedence."""
        result = processor._pick_coord("12.5", 10.0)
        assert result == 12.5

    def test_pick_coord_falls_back_to_exif(self, processor):
        """EXIF value used when form is None."""
        result = processor._pick_coord(None, 10.0)
        assert result == 10.0

    def test_pick_coord_defaults_to_zero(self, processor):
        """Zero when both are None."""
        result = processor._pick_coord(None, None)
        assert result == 0.0
