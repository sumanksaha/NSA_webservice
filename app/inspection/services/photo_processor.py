"""Photo processing: EXIF extraction, coordinate fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image


@dataclass
class ProcessedPhoto:
    """Result of photo processing with EXIF and coordinate resolution."""

    raw_lat: float | None
    raw_lng: float | None
    accuracy: float | None
    captured_at: str | None
    filepath: str
    filename: str
    mime_type: str | None
    size: int


class PhotoProcessor:
    """Core photo processing: EXIF extraction, coordinate fallback.

    Single interface: process(file, form_data) -> ProcessedPhoto
    """

    _ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "webp", "heic"})

    def process(
        self,
        file_obj,
        form_data: dict[str, Any],
        inspection_id: int | None = None,
    ) -> ProcessedPhoto:
        """Process photo: validate, extract EXIF, resolve coordinates."""
        # Validate file
        self._validate_file(file_obj)

        # Extract EXIF GPS
        exif_lat, exif_lng, exif_accuracy = self._extract_exif_gps(file_obj)

        # Coordinate fallback: form > EXIF > 0.0
        resolved_lat = self._pick_coord(form_data.get("lat"), exif_lat)
        resolved_lng = self._pick_coord(form_data.get("lng"), exif_lng)
        resolved_acc = self._pick_coord(form_data.get("accuracy"), exif_accuracy if exif_accuracy is not None else 0.0)

        # Parse captured_at
        captured_at_str = form_data.get("captured_at")
        if not captured_at_str:
            raise ValueError("captured_at must be a valid ISO format datetime string")

        # Save to temp
        import mimetypes
        import os
        import uuid

        from flask import current_app
        from werkzeug.utils import secure_filename

        image_id = str(uuid.uuid4())
        filename = secure_filename(file_obj.filename)
        temp_dir = Path(current_app.instance_path) / "temp_uploads"
        try:
            os.makedirs(str(temp_dir), exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"Failed to create temp dir: {e}") from e
        temp_path = temp_dir / f"{image_id}_{filename}"
        file_obj.save(str(temp_path))

        return ProcessedPhoto(
            raw_lat=resolved_lat,
            raw_lng=resolved_lng,
            accuracy=resolved_acc,
            captured_at=captured_at_str,
            filepath=str(temp_path),
            filename=filename,
            mime_type=mimetypes.guess_type(filename)[0],
            size=os.path.getsize(str(temp_path)),
        )

    def _validate_file(self, file_obj) -> None:
        """Validate file extension and PIL integrity."""
        if not file_obj.filename:
            raise ValueError("No filename provided")

        ext = Path(file_obj.filename).suffix.lower().lstrip(".")
        if ext not in self._ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension '.{ext}'. Allowed: {', '.join(sorted(self._ALLOWED_EXTENSIONS))}"
            )

        # PIL verify
        try:
            img = Image.open(file_obj)
            img.verify()
            file_obj.seek(0)
        except Exception as e:
            raise ValueError(f"Invalid image file: {e}") from e

    @staticmethod
    def _extract_exif_gps(file_obj) -> tuple[float | None, float | None, float | None]:
        """Extract GPS latitude, longitude, and accuracy from EXIF."""
        try:
            img = Image.open(file_obj)
            exif = img.getexif()
            if not exif:
                return None, None, None

            gps_info: dict[str, Any] = {}
            for tag, value in exif.items():
                decoded = ExifTags.TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    for gps_tag in value:
                        gps_decoded = ExifTags.GPSTAGS.get(gps_tag, gps_tag)
                        if gps_decoded is not None:
                            gps_info[gps_decoded] = value[gps_tag]

            def _convert_to_degrees(ref: str | None, values) -> float | None:
                if not values or len(values) < 3:
                    return None
                d, m, s = values
                try:
                    deg = float(d) + float(m) / 60.0 + float(s) / 3600.0
                    if ref in ("S", "W"):
                        deg = -deg
                    return deg
                except Exception:
                    return None

            lat = _convert_to_degrees(gps_info.get("GPSLatitudeRef"), gps_info.get("GPSLatitude"))
            lng = _convert_to_degrees(gps_info.get("GPSLongitudeRef"), gps_info.get("GPSLongitude"))

            accuracy: float | None = None
            if "GPSAltitude" in gps_info:
                alt = gps_info["GPSAltitude"]
                try:
                    accuracy = float(alt)
                except Exception:
                    try:
                        accuracy = float(alt[0]) / float(alt[1])
                    except Exception:
                        accuracy = None

            file_obj.seek(0)
            return lat, lng, accuracy
        except Exception:
            return None, None, None

    @staticmethod
    def _pick_coord(form_value: str | None, fallback: float | None) -> float:
        """Resolve coordinate from form value, falling back to EXIF, then 0.0."""
        if form_value is not None and str(form_value).strip() != "":
            try:
                return float(form_value)
            except (TypeError, ValueError):
                pass
        if fallback is not None:
            try:
                return float(fallback)
            except (TypeError, ValueError):
                pass
        return 0.0
