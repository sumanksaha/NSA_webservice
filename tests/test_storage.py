"""Unit tests for app.utils.storage.

Covers the Cloudinary storage helpers and the retry/error-handling logic:

* Cloudinary backend detection, lazy SDK loading, public-id parsing, upload
  and delete.
* The boto3 client retry configuration (botocore standard-mode retries).
* ``upload_photo`` validation (extension / size) and the Cloudinary -> R2
  fallback path.
* ``delete_photo`` routing between Cloudinary and R2 plus the idempotent
  "already absent" (NoSuchKey / 404) handling.

No network or real Cloudinary/R2 calls are made — boto3 and the cloudinary
SDK are fully mocked, and the module-level singletons are reset per test.
"""

import io
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

import app.utils.storage as storage
from app.utils.storage import (
    MAX_FILE_SIZE,
    _build_url,
    _cloudinary_configured,
    _delete_from_cloudinary,
    _extract_cloudinary_public_id,
    _extract_key,
    _get_bucket,
    _get_client,
    _get_cloudinary,
    _get_content_type,
    _upload_to_cloudinary,
    delete_photo,
    upload_photo,
)

# Environment variable groups managed by the reset fixture.
_R2_ENV = ["R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET", "R2_ENDPOINT", "R2_PUBLIC_BASE_URL", "R2_REGION"]
_CLOUDINARY_ENV = ["CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"]


@pytest.fixture(autouse=True)
def _reset_storage(monkeypatch):
    """Reset module singletons + clear storage env vars for every test."""
    storage._client = None
    storage._cloudinary = None
    for var in _R2_ENV + _CLOUDINARY_ENV:
        monkeypatch.delenv(var, raising=False)
    # Pin known R2 values so helper functions reading os.environ directly are
    # deterministic regardless of the host environment.
    monkeypatch.setenv("R2_BUCKET", "nsa-evidence")
    monkeypatch.setenv("R2_ENDPOINT", "https://r2.example.com")
    monkeypatch.setenv("R2_ACCESS_KEY", "test-key")
    monkeypatch.setenv("R2_SECRET_KEY", "test-secret")
    yield
    storage._client = None
    storage._cloudinary = None


def _cloudinary_env_set(monkeypatch):
    """Mark all Cloudinary env vars as present."""
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "123")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "secret")


def _patch_cloudinary_sdk(monkeypatch, fake=None):
    """Install the fake cloudinary module into sys.modules."""
    fake = fake or _FakeCloudinary()
    monkeypatch.setitem(sys.modules, "cloudinary", fake)
    monkeypatch.setitem(sys.modules, "cloudinary.uploader", fake.uploader)
    return fake


def _break_cloudinary_sdk(monkeypatch):
    """Make ``import cloudinary`` raise ImportError (SDK not installed)."""
    monkeypatch.setitem(sys.modules, "cloudinary", None)
    monkeypatch.setitem(sys.modules, "cloudinary.uploader", None)


def _patch_boto3_client(monkeypatch, client_obj):
    """Replace boto3.client with a factory returning ``client_obj``."""
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client_obj)
    return client_obj


def _make_client_error(code):
    """Build a botocore ClientError mimicking a delete_object failure."""
    return ClientError({"Error": {"Code": code, "Message": "fail"}}, "DeleteObject")


# ---------------------------------------------------------------------------
# Internal URL/key helpers
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_uses_endpoint_and_bucket_when_no_custom_domain(self):
        url = _build_url("inspections/42/abc.jpg")
        assert url == "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"

    def test_custom_domain_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.nsa.gov/")
        assert _build_url("inspections/42/abc.jpg") == "https://cdn.nsa.gov/inspections/42/abc.jpg"

    def test_custom_domain_no_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.nsa.gov")
        assert _build_url("key-with-no-ext") == "https://cdn.nsa.gov/key-with-no-ext"


class TestExtractKey:
    def test_strips_bucket_from_path_style_url(self):
        url = "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"
        assert _extract_key(url) == "inspections/42/abc.jpg"

    def test_custom_domain_url_keeps_key(self, monkeypatch):
        monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://cdn.nsa.gov")
        assert _extract_key("https://cdn.nsa.gov/inspections/42/abc.jpg") == "inspections/42/abc.jpg"

    def test_url_decodes_percent_encoding(self):
        url = "https://r2.example.com/nsa-evidence/inspections/42/a%20b%2Fc.jpg"
        assert _extract_key(url) == "inspections/42/a b/c.jpg"

    def test_non_matching_url_returns_full_path(self):
        assert _extract_key("https://other.example.com/inspections/42/abc.jpg") == "inspections/42/abc.jpg"


class TestContentTypes:
    @pytest.mark.parametrize(
        ("ext", "expected"),
        [
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".png", "image/png"),
            (".webp", "image/webp"),
            (".heic", "image/heic"),
            (".JPG", "image/jpeg"),  # case-insensitive
            (".gif", "application/octet-stream"),  # unknown -> fallback
        ],
    )
    def test_content_type_mapping(self, ext, expected):
        assert _get_content_type(ext) == expected


class TestGetBucket:
    def test_returns_bucket_when_set(self):
        assert _get_bucket() == "nsa-evidence"

    def test_raises_when_bucket_missing(self, monkeypatch):
        monkeypatch.delenv("R2_BUCKET", raising=False)
        with pytest.raises(RuntimeError, match="R2_BUCKET"):
            _get_bucket()


# ---------------------------------------------------------------------------
# Client / retry configuration
# ---------------------------------------------------------------------------


class TestClientRetryConfig:
    def test_client_built_with_retry_config(self, monkeypatch):
        """The boto3 client must carry the documented retry policy so transient
        failures (throttling / 5xx) are retried by botocore."""
        captured = {}

        def fake_client(service, **kwargs):
            captured["service"] = service
            captured["kwargs"] = kwargs
            return "the-client"

        monkeypatch.setattr(boto3, "client", fake_client)
        client = _get_client()

        assert client == "the-client"
        assert captured["service"] == "s3"
        assert captured["kwargs"]["endpoint_url"] == "https://r2.example.com"
        assert captured["kwargs"]["aws_access_key_id"] == "test-key"
        assert captured["kwargs"]["aws_secret_access_key"] == "test-secret"
        # Region defaults to 'auto' for R2 when R2_REGION is unset.
        assert captured["kwargs"]["region_name"] == "auto"

        config = captured["kwargs"]["config"]
        # botocore standard-mode retry: 1 initial + 1 retry = 2 attempts.
        assert config.retries == {"max_attempts": 2, "mode": "standard"}
        assert config.s3 == {"addressing_style": "path"}

    def test_client_uses_explicit_region_when_set(self, monkeypatch):
        monkeypatch.setenv("R2_REGION", "us-east-1")
        seen = {}

        def fake_client(service, **kwargs):
            seen.update(kwargs)
            return "client"

        monkeypatch.setattr(boto3, "client", fake_client)
        _get_client()
        assert seen["region_name"] == "us-east-1"

    def test_client_is_singleton(self, monkeypatch):
        calls = []

        def fake_client(service, **kwargs):
            calls.append(kwargs)
            return "the-client"

        monkeypatch.setattr(boto3, "client", fake_client)
        c1 = _get_client()
        c2 = _get_client()
        assert c1 is c2
        assert c2 == "the-client"
        assert len(calls) == 1  # only built once

    def test_get_client_raises_when_creds_missing(self, monkeypatch):
        for var in ["R2_ACCESS_KEY", "R2_SECRET_KEY"]:
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError, match="R2 storage is not configured"):
            _get_client()


# ---------------------------------------------------------------------------
# Cloudinary configuration / loading
# ---------------------------------------------------------------------------


class TestCloudinaryConfigured:
    def test_false_when_any_missing(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        monkeypatch.delenv("CLOUDINARY_API_SECRET", raising=False)
        assert _cloudinary_configured() is False

    def test_true_when_all_present(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        assert _cloudinary_configured() is True

    def test_false_when_none(self):
        assert _cloudinary_configured() is False


class TestGetCloudinary:
    def test_caches_configured_module(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)

        result = _get_cloudinary()
        assert result is fake
        assert fake._configured is True
        # Second call returns the cached handle (no reconfigure).
        assert _get_cloudinary() is fake

    def test_returns_none_when_sdk_not_installed(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        _break_cloudinary_sdk(monkeypatch)
        assert _get_cloudinary() is None


class TestCloudinaryPublicIdExtraction:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "https://res.cloudinary.com/demo/image/upload/v1234567890/inspections/42/abc123.jpg",
                "inspections/42/abc123",
            ),
            (
                "https://res.cloudinary.com/demo/image/upload/inspections/42/abc123.jpg",
                "inspections/42/abc123",
            ),
            (
                "https://res.cloudinary.com/demo/image/upload/inspections/42/sub/nested.jpg",
                "inspections/42/sub/nested",
            ),
            # percent-encoded public ids are decoded
            (
                "https://res.cloudinary.com/demo/image/upload/inspections/42/a%20b.jpg",
                "inspections/42/a b",
            ),
        ],
    )
    def test_extracts_public_id(self, url, expected):
        assert _extract_cloudinary_public_id(url) == expected

    def test_returns_none_for_non_cloudinary_url(self):
        assert _extract_cloudinary_public_id("https://cdn.example/x.jpg") is None

    def test_returns_none_when_no_upload_segment(self):
        assert _extract_cloudinary_public_id("https://res.cloudinary.com/demo/image/abc.jpg") is None


# ---------------------------------------------------------------------------
# Cloudinary upload / delete
# ---------------------------------------------------------------------------


class TestUploadToCloudinary:
    def test_upload_returns_secure_url_and_seeks(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)

        buf = io.BytesIO(b"image-bytes")
        # Simulate the pointer not being at 0 to prove seek(0) is called.
        buf.seek(0, io.SEEK_END)

        url = _upload_to_cloudinary(buf, 42)
        called_with = fake.uploader.upload_called_with
        assert called_with is not None
        public_id = called_with["public_id"]
        assert url == fake.secure_url(public_id)
        assert public_id.startswith("inspections/42/")
        assert buf.tell() == 0  # seek(0) was invoked
        assert called_with["resource_type"] == "image"

    def test_returns_none_when_sdk_missing(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        _break_cloudinary_sdk(monkeypatch)
        assert _upload_to_cloudinary(io.BytesIO(b"x"), 42) is None


class TestDeleteFromCloudinary:
    def test_success(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)
        fake.uploader.destroy_result = {"result": "ok"}
        url = "https://res.cloudinary.com/demo/image/upload/inspections/42/abc.jpg"
        assert _delete_from_cloudinary(url) is True
        called_with = fake.uploader.destroy_called_with
        assert called_with is not None
        assert called_with == {"public_id": "inspections/42/abc", "resource_type": "image"}

    def test_returns_false_on_error_result(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)
        fake.uploader.destroy_result = {"result": "error", "error": "not found"}
        assert _delete_from_cloudinary("https://res.cloudinary.com/demo/image/upload/inspections/42/abc.jpg") is False

    def test_returns_false_on_exception(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)
        fake.uploader.destroy_raises = RuntimeError("network")
        assert _delete_from_cloudinary("https://res.cloudinary.com/demo/image/upload/inspections/42/abc.jpg") is False

    def test_returns_false_when_url_not_cloudinary(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        _patch_cloudinary_sdk(monkeypatch)
        assert _delete_from_cloudinary("https://cdn.example/nope.jpg") is False

    def test_returns_false_when_sdk_missing(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        _break_cloudinary_sdk(monkeypatch)
        assert _delete_from_cloudinary("https://res.cloudinary.com/demo/image/upload/inspections/42/abc.jpg") is False


# ---------------------------------------------------------------------------
# upload_photo — validation + Cloudinary/R2 routing + fallback
# ---------------------------------------------------------------------------


class TestUploadPhotoValidation:
    def test_rejects_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file extension"):
            upload_photo(io.BytesIO(b"data"), 42, "photo.gif")

    def test_rejects_oversized_file_mocked(self):
        # tell() reports a size above the 5 MB ceiling without allocating it.
        file_obj = MagicMock()
        file_obj.tell.return_value = MAX_FILE_SIZE + 1
        with pytest.raises(ValueError, match="exceeds the maximum allowed size"):
            upload_photo(file_obj, 42, "photo.png")

    def test_rejects_oversized_real_file(self):
        """A genuinely large BytesIO must also be rejected (real path)."""
        big = io.BytesIO(b"\x00" * (MAX_FILE_SIZE + 1))
        with pytest.raises(ValueError, match="exceeds the maximum allowed size"):
            upload_photo(big, 42, "photo.png")


class TestUploadPhotoCloudinaryPath:
    def test_routes_to_cloudinary_when_configured(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)

        url = upload_photo(io.BytesIO(b"image-bytes"), 42, "photo.jpg")
        called_with = fake.uploader.upload_called_with
        assert called_with is not None
        public_id = called_with["public_id"]
        assert url == fake.secure_url(public_id)
        # Cloudinary path must NOT touch the R2 client.
        assert storage._client is None

    def test_falls_back_to_r2_when_sdk_missing(self, monkeypatch):
        """When Cloudinary is configured but the SDK is absent, upload_photo
        must transparently fall back to the R2/B2 backend."""
        _cloudinary_env_set(monkeypatch)
        _break_cloudinary_sdk(monkeypatch)

        put_calls = []

        class FakeClient:
            def put_object(self, **kwargs):
                put_calls.append(kwargs)

        _patch_boto3_client(monkeypatch, FakeClient())

        url = upload_photo(io.BytesIO(b"image-bytes"), 42, "photo.jpg")
        assert url.endswith(".jpg") and url.startswith("https://")
        assert len(put_calls) == 1
        assert put_calls[0]["Bucket"] == "nsa-evidence"
        assert put_calls[0]["Key"].startswith("inspections/42/")
        assert put_calls[0]["ContentType"] == "image/jpeg"


# ---------------------------------------------------------------------------
# delete_photo — routing + idempotent retry-on-absent
# ---------------------------------------------------------------------------


class TestDeletePhotoRouting:
    def test_cloudinary_url_routes_to_cloudinary(self, monkeypatch):
        _cloudinary_env_set(monkeypatch)
        fake = _patch_cloudinary_sdk(monkeypatch)
        fake.uploader.destroy_result = {"result": "ok"}

        url = "https://res.cloudinary.com/demo/image/upload/inspections/42/abc.jpg"
        assert delete_photo(url) is True
        called_with = fake.uploader.destroy_called_with
        assert called_with is not None
        assert called_with["public_id"] == "inspections/42/abc"

    def test_r2_url_deletes_via_client(self, monkeypatch):
        deleted = {}

        class FakeClient:
            def delete_object(self, **kwargs):
                deleted.update(kwargs)

        _patch_boto3_client(monkeypatch, FakeClient())
        url = "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"
        assert delete_photo(url) is True
        assert deleted == {"Bucket": "nsa-evidence", "Key": "inspections/42/abc.jpg"}


class TestDeletePhotoIdempotency:
    """delete_photo treats an already-absent object as success — this is the
    retry-safe behaviour for concurrent/retried deletes."""

    @pytest.mark.parametrize("code", ["NoSuchKey", "404"])
    def test_absent_object_is_success(self, monkeypatch, code):
        _patch_boto3_client(monkeypatch, _RaisingClient(_make_client_error(code)))
        url = "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"
        assert delete_photo(url) is True

    def test_other_client_error_returns_false(self, monkeypatch):
        _patch_boto3_client(monkeypatch, _RaisingClient(_make_client_error("AccessDenied")))
        url = "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"
        assert delete_photo(url) is False

    def test_unexpected_exception_returns_false(self, monkeypatch):
        _patch_boto3_client(monkeypatch, _RaisingClient(ConnectionError("boom")))
        url = "https://r2.example.com/nsa-evidence/inspections/42/abc.jpg"
        assert delete_photo(url) is False

    def test_unparsable_key_returns_false(self):
        # A URL with no usable path yields an empty key -> no delete attempted.
        assert delete_photo("not-a-url") is False


# ---------------------------------------------------------------------------
# upload_photo R2 retry path: put_object failure re-raises (botocore retries
# are exercised at the client config level, see TestClientRetryConfig).
# ---------------------------------------------------------------------------


class TestUploadPhotoR2Retry:
    def test_put_object_failure_reraises(self, monkeypatch):
        """A hard failure from put_object is logged and re-raised so the caller
        (and the botocore retry policy) can act on it."""

        class ExplodingClient:
            def put_object(self, **kwargs):
                raise ClientError({"Error": {"Code": "500", "Message": "oops"}}, "PutObject")

        _patch_boto3_client(monkeypatch, ExplodingClient())
        with pytest.raises(ClientError):
            upload_photo(io.BytesIO(b"image-bytes"), 42, "photo.jpg")


# ---------------------------------------------------------------------------
# Fake cloudinary SDK double
# ---------------------------------------------------------------------------


class _FakeUploader:
    """Stand-in for ``cloudinary.uploader`` with call recording."""

    def __init__(self, parent):
        self._parent = parent
        self.upload_called_with: dict | None = None
        self.destroy_called_with: dict | None = None
        self.destroy_result = {"result": "ok"}
        self.destroy_raises: Exception | None = None

    def upload(self, file_obj, public_id=None, resource_type=None):
        self.upload_called_with = {
            "file_obj": file_obj,
            "public_id": public_id,
            "resource_type": resource_type,
        }
        return {"secure_url": self._parent.secure_url(public_id)}

    def destroy(self, public_id, resource_type=None):
        self.destroy_called_with = {"public_id": public_id, "resource_type": resource_type}
        if self.destroy_raises is not None:
            raise self.destroy_raises
        return self.destroy_result


class _FakeCloudinary:
    """Minimal in-memory cloudinary module double."""

    def __init__(self):
        self.uploader = _FakeUploader(self)
        self._configured = False
        self._config = {}

    def config(self, **kwargs):
        self._configured = True
        self._config.update(kwargs)

    def secure_url(self, public_id):
        return f"https://res.cloudinary.com/demo/image/upload/v1/{public_id}.jpg"


class _RaisingClient:
    """A fake S3 client whose delete always raises a fixed exception."""

    def __init__(self, exc):
        self._exc = exc

    def delete_object(self, **kwargs):
        raise self._exc
