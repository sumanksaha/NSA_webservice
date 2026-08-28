"""Resolve an FSO name to their signature image file.

Convention: ``signature/{name_lower_no_spaces}.jpg``
(e.g. ``signature/sumansaha.jpg`` for "Suman Saha").
"""

from __future__ import annotations

import base64
import os
from pathlib import Path


# Root of the signature directory (project root / signature/)
_SIGNATURE_DIR = Path(__file__).resolve().parent.parent.parent / "signature"


def get_signature_path(fso_name: str | None) -> Path | None:
    """Return the filesystem path to *fso_name*'s signature image, or ``None``."""
    if not fso_name:
        return None
    filename = fso_name.lower().replace(" ", "") + ".jpg"
    path = _SIGNATURE_DIR / filename
    return path if path.is_file() else None


def get_signature_data_uri(fso_name: str | None) -> str | None:
    """Return a ``data:image/jpeg;base64,...`` URI for embedding in HTML emails."""
    path = get_signature_path(fso_name)
    if path is None:
        return None
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def get_signature_bytes(fso_name: str | None) -> bytes | None:
    """Return raw image bytes (for embedding in .docx), or ``None``."""
    path = get_signature_path(fso_name)
    if path is None:
        return None
    return path.read_bytes()
