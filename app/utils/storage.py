"""S3-compatible storage utilities for the NSA webservice.

Provides lazy-initialised, singleton boto3 client for Cloudflare R2 /
Backblaze B2 and thin wrappers for uploading and deleting inspection
photo evidence.

Configuration is read from environment variables:

    R2_ACCESS_KEY        - S3 access key (required at call-time)
    R2_SECRET_KEY        - S3 secret key (required at call-time)
    R2_BUCKET            - Target bucket name (required at call-time)
    R2_ENDPOINT          - S3-compatible endpoint URL (required at call-time)
    R2_PUBLIC_BASE_URL   - Optional custom domain for public URLs
    R2_REGION            - Optional region override (defaults to 'auto' for R2)

The client is **not** created at import time.  If the environment variables
are missing, a clear ``RuntimeError`` is raised only when ``upload_photo`` or
``delete_photo`` is actually called, so application startup is never blocked.
"""

import logging
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton - stays ``None`` until first use so that missing
# credentials never crash application startup.
# ---------------------------------------------------------------------------
_client = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic"})
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_client():
    """Lazily create and return the S3-compatible storage client (singleton).

    Raises ``RuntimeError`` with a descriptive message if any of the required
    environment variables are missing.
    """
    global _client
    if _client is not None:
        return _client

    # Imported here so that the module is importable even when boto3 is not
    # installed — the error only surfaces when storage is actually used.
    import boto3
    from botocore.config import Config

    access_key = os.environ.get("R2_ACCESS_KEY")
    secret_key = os.environ.get("R2_SECRET_KEY")
    bucket = os.environ.get("R2_BUCKET")
    endpoint = os.environ.get("R2_ENDPOINT")

    _required = [
        ("R2_ACCESS_KEY", access_key),
        ("R2_SECRET_KEY", secret_key),
        ("R2_BUCKET", bucket),
        ("R2_ENDPOINT", endpoint),
    ]
    missing = [name for name, val in _required if not val]
    if missing:
        raise RuntimeError(
            "R2 storage is not configured. The following environment "
            f"variable(s) are required: {', '.join(missing)}. "
            "Set R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, and R2_ENDPOINT "
            "before calling upload_photo() or delete_photo().",
        )

    # botocore built-in retry config: 2 total attempts (1 initial + 1 retry)
    # on transient errors (throttling, 5xx, connection resets).
    config = Config(
        retries={"max_attempts": 2, "mode": "standard"},
        s3={"addressing_style": "path"},
    )

    _client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config,
        region_name=os.environ.get("R2_REGION", "auto"),
    )
    logger.info(
        "R2 storage client initialised (bucket=%s, endpoint=%s)",
        bucket,
        endpoint,
    )
    return _client


def _get_bucket():
    """Return the configured bucket name or raise RuntimeError."""
    bucket = os.environ.get("R2_BUCKET")
    if not bucket:
        raise RuntimeError("R2_BUCKET environment variable is not set.")
    return bucket


def _build_url(key):
    """Construct the public/accessible URL for an object key.

    If ``R2_PUBLIC_BASE_URL`` is set (custom domain / Cloudflare R2 public
    bucket), the URL is ``{R2_PUBLIC_BASE_URL}/{key}``.  Otherwise it is
    constructed from the endpoint and bucket: ``{R2_ENDPOINT}/{R2_BUCKET}/{key}``.
    """
    public_base = os.environ.get("R2_PUBLIC_BASE_URL")
    if public_base:
        return f"{public_base.rstrip('/')}/{key}"
    endpoint = os.environ.get("R2_ENDPOINT", "")
    bucket = os.environ.get("R2_BUCKET", "")
    return f"{endpoint.rstrip('/')}/{bucket}/{key}"


def _extract_key(file_url):
    """Extract the S3 object key from a stored photo URL.

    Handles both custom-domain URLs (``R2_PUBLIC_BASE_URL/key``) and path-style
    URLs (``{endpoint}/{bucket}/key``).
    """
    parsed = urlparse(file_url)
    path = unquote(parsed.path).lstrip("/")
    bucket = os.environ.get("R2_BUCKET", "")
    # Strip a leading bucket-name segment that appears in path-style URLs.
    if bucket and path.startswith(f"{bucket}/"):
        path = path[len(bucket) + 1 :]
    return path


def _get_content_type(ext):
    """Map a file extension to its MIME content type."""
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def upload_photo(file_obj, adjudication_id, filename):
    """Upload a photo to R2/B2-compatible storage.

    Parameters
    ----------
    file_obj : file-like
        A seekable file-like object containing the image data.
    adjudication_id : int
        The adjudication this photo belongs to (used in the object key).
    filename : str
        Original filename - used to determine the extension.

    Returns
    -------
    str
        The public/accessible URL of the uploaded photo.

    Raises
    ------
    RuntimeError
        If R2 credentials are not configured.
    ValueError
        If the file extension is not allowed or the file exceeds 5 MB.

    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}'. Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # --- Size validation via seek/tell ---------------------------------------
    try:
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise ValueError(f"Unable to determine file size: {exc}") from exc

    if size > MAX_FILE_SIZE:
        raise ValueError(f"File size {size} bytes exceeds the maximum allowed size of {MAX_FILE_SIZE} bytes (5 MB).")

    # --- Collision-safe object key ------------------------------------------
    key = f"inspections/{adjudication_id}/{uuid4().hex}{ext}"
    content_type = _get_content_type(ext)

    client = _get_client()
    bucket = _get_bucket()

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=file_obj,
            ContentType=content_type,
        )
    except Exception:
        logger.exception("Failed to upload photo to storage (key=%s)", key)
        raise

    url = _build_url(key)
    logger.info("Uploaded photo (key=%s, size=%d bytes) -> %s", key, size, url)
    return url


def delete_photo(file_url):
    """Delete a photo from storage by its URL.

    Parameters
    ----------
    file_url : str
        The public URL returned by :func:`upload_photo`.

    Returns
    -------
    bool
        ``True`` if the object was deleted (or was already absent),
        ``False`` on a genuine error.

    """
    from botocore.exceptions import ClientError

    key = _extract_key(file_url)
    if not key:
        logger.warning(
            "delete_photo: could not extract object key from URL: %s",
            file_url,
        )
        return False

    client = _get_client()
    bucket = _get_bucket()

    try:
        client.delete_object(Bucket=bucket, Key=key)
        logger.info("Deleted photo (key=%s)", key)
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        # NoSuchKey / 404 — the object is already gone, which is the desired
        # end-state, so treat it as success rather than raising.
        if error_code in ("NoSuchKey", "404"):
            logger.info("delete_photo: object already absent (key=%s)", key)
            return True
        logger.error("delete_photo: failed to delete object (key=%s): %s", key, exc)
        return False
    except Exception:
        logger.exception("delete_photo: unexpected error (key=%s)", key)
        return False


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Verify credentials end-to-end: upload a tiny in-memory image, then
    # delete it.  Run with:  python -m app.utils.storage
    from io import BytesIO

    from PIL import Image

    required = ["R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET", "R2_ENDPOINT"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise SystemExit(0)

    # Create a tiny 1x1 red PNG entirely in memory.
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    test_adjudication_id = 0
    test_filename = "smoke_test.png"

    # --- Upload ---------------------------------------------------------------
    try:
        url = upload_photo(buf, test_adjudication_id, test_filename)
    except Exception:
        raise SystemExit(1) from None

    # --- Delete ---------------------------------------------------------------
    try:
        result = delete_photo(url)
        if result:
            pass
        else:
            raise SystemExit(1) from None
    except Exception:
        raise SystemExit(1) from None
