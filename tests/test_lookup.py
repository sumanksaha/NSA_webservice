"""Caller-contract tests for the LicenseLookup seam (D8).

Locks the ``LookupResult`` shape, the pure JSON-repair helper, the injectable
rate limiter, and the never-raises contract across both lookups so a future
refactor cannot silently revert to tuple unpacking or raised exceptions.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from app.utils.lookup import (
    LookupResult,
    RateLimiter,
    _NoopRateLimiter,
    lookup_ce,
    lookup_fssai,
    repair_kmc_json,
)


# ---------------------------------------------------------------------------
# LookupResult value contract
# ---------------------------------------------------------------------------


def test_lookup_result_found_shape():
    r = LookupResult(found=True, data={"companyName": "Acme"})
    assert r.found is True
    assert r.error is None
    assert r.data == {"companyName": "Acme"}
    assert bool(r) is True
    assert r.as_tuple() == ({"companyName": "Acme"}, None)


def test_lookup_result_not_found_shape():
    r = LookupResult(found=False)
    assert r.found is False
    assert r.error is None
    assert r.data == {}
    assert bool(r) is False
    assert r.as_tuple() == (None, None)


def test_lookup_result_error_shape():
    r = LookupResult(found=False, error="boom")
    assert r.found is False
    assert r.error == "boom"
    assert bool(r) is False
    assert r.as_tuple() == (None, "boom")


# ---------------------------------------------------------------------------
# repair_kmc_json pure function
# ---------------------------------------------------------------------------


def test_repair_kmc_json_success():
    parsed = repair_kmc_json('{"success": true, licenseNo: [[{licNo: "K1", licClosingDate: ""}]]}')
    assert parsed == {"success": True, "licenseNo": [[{"licNo": "K1", "licClosingDate": ""}]]}


def test_repair_kmc_json_failure():
    assert repair_kmc_json("this is not json at all") is None


# ---------------------------------------------------------------------------
# RateLimiter adapter
# ---------------------------------------------------------------------------


def test_rate_limiter_creates_parent_dir(tmp_path):
    lock = tmp_path / "missing" / "still-missing" / ".kmc_lookup_lock"
    ts = tmp_path / "missing" / "still-missing" / ".kmc_last_request_time"
    RateLimiter(lock_path=lock, timestamp_path=ts, min_gap_seconds=0).acquire()
    assert lock.exists()
    assert ts.exists()


# ---------------------------------------------------------------------------
# lookup_fssai
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    from app import create_app
    from app.extensions import db

    application = create_app()
    ctx = application.app_context()
    ctx.push()
    db.create_all()
    yield application
    db.session.remove()
    ctx.pop()


def test_lookup_fssai_empty_returns_error(app):
    result = lookup_fssai("")
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert isinstance(result.error, str)


def test_lookup_fssai_bad_prefix_returns_error(app):
    result = lookup_fssai("3123456789")
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert isinstance(result.error, str)


def test_lookup_fssai_seeded_returns_found(app):
    from app.extensions import db
    from app.models.lookup import FssaiLicense

    license_no = "11522000000482"
    if db.session.get(FssaiLicense, license_no) is None:
        db.session.add(
            FssaiLicense(
                license_no=license_no,
                company_name="Test Foods Pvt Ltd",
                full_address="1 Test Lane, Kolkata",
                expiry_date="31-12-2027",
            ),
        )
        db.session.commit()
    db.session.expunge_all()

    result = lookup_fssai(license_no)
    assert isinstance(result, LookupResult)
    assert result.found is True
    assert result.error is None
    assert "companyName" in result.data


def test_lookup_fssai_not_found(app):
    result = lookup_fssai("19999999999999")
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# lookup_ce — never raises, injectable seams
# ---------------------------------------------------------------------------


def _stub_http(text: str):
    client = mock.Mock()
    client.warm.return_value = None
    resp = mock.Mock()
    resp.text = text
    client.post.return_value = resp
    return client


def test_lookup_ce_empty_returns_error():
    result = lookup_ce("")
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert isinstance(result.error, str)


def test_lookup_ce_success():
    result = lookup_ce(
        "K1",
        rate_limiter=_NoopRateLimiter(),
        http_client=_stub_http('{"success": true, licenseNo: [[{licNo: "K1", licClosingDate: ""}]]}'),
    )
    assert isinstance(result, LookupResult)
    assert result.found is True
    assert result.error is None
    assert result.data["identity"]["licNo"] == "K1"


def test_lookup_ce_not_found():
    result = lookup_ce(
        "K1",
        rate_limiter=_NoopRateLimiter(),
        http_client=_stub_http('{"success": false}'),
    )
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert result.error is None


def test_lookup_ce_malformed_json():
    result = lookup_ce(
        "K1",
        rate_limiter=_NoopRateLimiter(),
        http_client=_stub_http("not json"),
    )
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert isinstance(result.error, str)


def test_lookup_ce_exploding_limiter_becomes_error():
    limiter = mock.Mock()
    limiter.acquire.side_effect = OSError("disk read-only")
    result = lookup_ce("K1", rate_limiter=limiter, http_client=_stub_http("{}"))
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert "disk read-only" in (result.error or "")


def test_lookup_ce_exploding_http_becomes_error():
    client = mock.Mock()
    client.warm.side_effect = RuntimeError("TLS boom")
    result = lookup_ce("K1", rate_limiter=_NoopRateLimiter(), http_client=client)
    assert isinstance(result, LookupResult)
    assert result.found is False
    assert "TLS boom" in (result.error or "")