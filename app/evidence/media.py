"""Image processing helpers for the evidence blueprint (Phase 5).

Pillow-based lossy compression and JPEG thumbnail generation for image
evidence. Both helpers are defensive: anything that is not a readable
image returns ``None`` and never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Longest edge (px) kept after compression; larger images are downscaled.
MAX_IMAGE_DIMENSION = 2560
JPEG_QUALITY = 82
THUMB_DIR_NAME = "evidence_thumbnails"
THUMB_SIZE = (320, 320)

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic"})


def is_image_path(path: Path) -> bool:
    """True when the file extension looks like an image we can thumbnail."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def compress_image(src: Path) -> Path | None:
    """Lossy-compress an image; returns the new path when rewritten.

    Downscales images whose longest edge exceeds ``MAX_IMAGE_DIMENSION``
    and re-encodes as optimized progressive JPEG. The original file is
    replaced only when the rewrite actually saved space; otherwise the
    original is kept. Returns ``None`` when the file is not a readable
    image or compression would not help.
    """
    from PIL import Image, UnidentifiedImageError

    out_path = src.with_name(src.stem + "_optimized.jpg")
    try:
        with Image.open(src) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            width, height = img.size
            if max(width, height) > MAX_IMAGE_DIMENSION:
                img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)
            img.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Image compression skipped for %s: %s", src, exc)
        out_path.unlink(missing_ok=True)
        return None

    if out_path.stat().st_size < src.stat().st_size:
        src.unlink(missing_ok=True)
        return out_path
    out_path.unlink(missing_ok=True)
    return None


def generate_thumbnail(src: Path, thumb_dir: Path, evidence_id: str) -> Path | None:
    """Create a JPEG thumbnail named after ``evidence_id``; returns its path.

    Returns ``None`` for non-image files or when generation fails.
    """
    from PIL import Image, UnidentifiedImageError

    if not is_image_path(src):
        return None
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{evidence_id}.jpg"
    try:
        with Image.open(src) as img:
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(thumb_path, "JPEG", quality=80, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("Thumbnail generation failed for %s: %s", src, exc)
        thumb_path.unlink(missing_ok=True)
        return None
    return thumb_path
