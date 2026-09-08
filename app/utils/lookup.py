"""License lookup utilities (shallow module deepened — D8).

Provides standardized ``LookupResult``-returning functions for the two domain
lookups this module backs:

* :func:`lookup_fssai` — FSSAI License/Registration number resolved against the
  Postgres reference tables ``fssai_licenses`` / ``fssai_registrations``
  (:mod:`app.models.lookup`).  Refreshed bi-monthly via
  ``scripts/load_fssai_lookup.py`` (runbook: ``docs/FSSAI_LOOKUP_REFRESH.md``).

* :func:`lookup_ce` — KMC Trade License details fetched from the KMC portal
  (``kmcgov.in``), subject to a cross-process rate-limit enforced by
  :class:`RateLimiter`.

Both functions return a :class:`LookupResult` dataclass — neither raises on the
"not found / bad input / external failure" paths that callers historically had
to catch, and neither returns a bare tuple.  See ``docs/INTERFACE-DESIGN.md`` for
the contract rationale.
"""

import json
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

from typing import Any

fcntl: Any
try:
    import fcntl
except ImportError:
    # fcntl is Unix-only; on Windows file locking is skipped (single-process
    # rate limiting still works via the timestamp file).
    fcntl = None

BASE_DIR = Path(__file__).parent.resolve()
# app/utils is nested two levels deep from the workspace root.
# Backs KMC rate-limit lock files (.kmc_lookup_lock / .kmc_last_request_time)
# used by lookup_ce(); must remain writable. The FSSAI reference data itself
# now lives in Postgres (app/models/lookup.py) — see docs/FSSAI_LOOKUP_POSTGRES_RESEARCH.md.
DB_DIR = (Path(__file__).parent.parent.parent).resolve() / "db"

# Rate limiting for KMC CE lookup (govt website - 40 second gap required)
_KMC_RATE_LIMIT_SECONDS = 40  # Minimum gap between KMC portal requests
_KMC_LOCK_PATH = DB_DIR / ".kmc_lookup_lock"
_KMC_LAST_REQUEST_TIME_PATH = DB_DIR / ".kmc_last_request_time"

# KMC endpoint constants
_KMC_PORTAL_BASE = "https://www.kmcgov.in/KMCPortal"
_KMC_TRADE_LICENSE_JSP = f"{_KMC_PORTAL_BASE}/jsp/TradeLicenseInformation.jsp"
_KMC_SEARCH_ACTION = (
    f"{_KMC_PORTAL_BASE}/LicenseInformationAction.do"
    "?passedParam=searchResult"
)

# TLS cipher override — KMC's Sectigo cert works at SECLEVEL=1.
_KMC_CIPHER_STRING = "DEFAULT@SECLEVEL=1"
_KMC_HTTP_TIMEOUT = 15  # seconds


# --------------------------------------------------------------------------- #
# LookupResult — unified return contract                                       #
# --------------------------------------------------------------------------- #


@dataclass
class LookupResult:
    """Standardized return type for license lookups.

    Callers (all Flask route handlers) branch on two booleans:

    * ``result.error is not None`` — caller should surface the message.
    * ``result.found`` — ``True`` when a record was resolved; ``False`` on
      not-found / failure (so ``found`` and ``error`` are mutually
      explanatory, not mutually exclusive).
    * ``result.data`` — the resolved payload dict (caller inspects keys
      relevant to its domain: FSSAI → ``companyName``/``fullAddress``/etc.;
      CE → ``identity``/``fee_heads``/``is_closed``).

    Invariant: when ``found`` is ``False`` and ``error`` is ``None``
    the lookup completed without error but found no row.
    """

    found: bool
    error: Optional[str] = None
    data: dict = field(default_factory=dict)

    # Backwards-compatible convenience for callers that still want a
    # tuple-style unpacking or `if result:` truthiness.
    def __bool__(self) -> bool:
        return self.found

    def as_tuple(self) -> tuple[Optional[dict], Optional[str]]:
        """Legacy compatibility — returns ``(data_or_None, error_or_None)``."""
        return (self.data if self.found else None), self.error


# --------------------------------------------------------------------------- #
# repair_kmc_json() — pure function                                            #
# --------------------------------------------------------------------------- #


def repair_kmc_json(raw_text: str) -> Optional[dict]:
    """Repair the KMC portal's unquoted-key JSON and parse it.

    Pure function (no I/O, no side effects).  The KMC search endpoint returns
    JSON whose object keys appear without surrounding double-quotes
    (``{"success": true, licenseNo: [...]}``), which is invalid JSON per
    RFC 8259.  This injects the missing quotes via regex and delegates to
    :func:`json.loads`.

    Returns the parsed ``dict``, or ``None`` if repair failed.
    """
    fixed_text = re.sub(
        r"([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:",
        r'\1"\2":',
        raw_text,
    )
    try:
        return json.loads(fixed_text)
    except json.JSONDecodeError as e:
        logger.error("KMC JSON repair failed: %s", e)
        return None


# --------------------------------------------------------------------------- #
# HTTP adapter (injectable seam for testability)                               #
# --------------------------------------------------------------------------- #


class KmcHttpClient(Protocol):
    """Protocol describing the HTTP operations :func:`lookup_ce` needs."""

    def get(self, url: str) -> httpx.Response: ...

    def post(
        self,
        url: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> httpx.Response: ...


class DefaultKmcHttpClient:
    """Concrete HTTP adapter that warms KMC's session cookies and reuses a
    single :class:`httpx.Client` with TLS verification enforced.

    Instantiating this adapter performs *no network I/O*; the warming GET
    happens lazily inside :meth:`warm` / :meth:`search`.
    """

    def __init__(self, timeout: float = _KMC_HTTP_TIMEOUT) -> None:
        self._timeout = timeout
        self._client: Optional[httpx.Client] = None

    # -- internal ----------------------------------------------------------------

    @property
    def _ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.set_ciphers(_KMC_CIPHER_STRING)
        return ctx

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                verify=self._ctx,
            )
        return self._client

    # -- Protocol methods --------------------------------------------------------

    def get(self, url: str) -> httpx.Response:
        return self.client.get(url)

    def post(
        self,
        url: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> httpx.Response:
        return self.client.post(url, data=data, headers=headers or {})

    # -- lifecycle ---------------------------------------------------------------

    def warm(self) -> None:
        """Perform the cookie-warming GET against the KMC portal JSP."""
        self.client.get(_KMC_TRADE_LICENSE_JSP)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "DefaultKmcHttpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# RateLimiter — file-lock + timestamp adapter (injectable seam)                #
# --------------------------------------------------------------------------- #


class RateLimiter:
    """Cross-process rate limiter using an advisory file lock + timestamp.

    Coordinates across WSGI workers: the lock file serializes access to the
    timestamp file so only one process can evaluate and update the gap at a
    time.  On platforms without ``fcntl`` (Windows) the lock is skipped but
    the timestamp file still enforces a best-effort single-process gap.

    The adapter is injectable so tests can pass a no-op or stub in place of
    real file I/O.
    """

    def __init__(
        self,
        lock_path: Path = _KMC_LOCK_PATH,
        timestamp_path: Path = _KMC_LAST_REQUEST_TIME_PATH,
        min_gap_seconds: float = _KMC_RATE_LIMIT_SECONDS,
    ) -> None:
        self._lock_path = lock_path
        self._timestamp_path = timestamp_path
        self._min_gap = min_gap_seconds

    def acquire(self) -> None:
        """Block until the minimum gap since the last request has elapsed,
        then record *now* as the new last-request time.

        Uses :data:`fcntl.flock` on Unix for cross-process serialisation.
        """
        lock_fd = None
        try:
            with open(self._lock_path, "w") as lock_fd:
                if fcntl:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

                try:
                    with open(self._timestamp_path) as f:
                        last_time = float(f.read().strip() or "0")
                except (FileNotFoundError, ValueError):
                    last_time = 0

                current_time = time.time()
                elapsed = current_time - last_time
                if elapsed < self._min_gap:
                    sleep_time = self._min_gap - elapsed
                    time.sleep(sleep_time)
                    current_time = time.time()

                with open(self._timestamp_path, "w") as f:
                    f.write(str(current_time))
        finally:
            if lock_fd and fcntl:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                except Exception as e:
                    logger.warning("Failed to release file lock: %s", e)


class _NoopRateLimiter:
    """Test / fallback adapter — does nothing, no I/O."""

    def acquire(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# lookup_fssai — Postgres-backed FSSAI license/registration lookup             #
# --------------------------------------------------------------------------- #


def lookup_fssai(license_no: str) -> LookupResult:
    """Look up an FSSAI License/Registration number (Postgres-backed).

    Exact primary-key match on the reference tables ``fssai_licenses`` /
    ``fssai_registrations`` (:mod:`app.models.lookup`), refreshed via
    ``scripts/load_fssai_lookup.py``.

    .. warning:: Naming inversion (historical, intentional): numbers starting
       with ``'1'`` belong to *Registration-category* FBOs even though they
       resolve to the **license** table (``fssai_licenses``), and vice versa
       for ``'2'`` -> ``fssai_registrations``.  The mapping is mechanical,
       not semantic — do not swap the tables.

    Returns a :class:`LookupResult` whose ``data`` dict carries
    ``companyName`` / ``fullAddress`` / ``expiryDate`` / ``source`` on success,
    or ``data == {}`` with ``error`` set on failure.

    Requires an active Flask application context (all callers are route
    handlers).
    """
    # Local imports avoid import cycles during app factory bootstrap
    # (blueprints import this module before extensions are fully wired).
    from app.extensions import db
    from app.models.lookup import FssaiLicense, FssaiRegistration

    if not license_no:
        return LookupResult(found=False, error="License/Registration number is required.")

    prefix = license_no[0]
    if prefix == "1":
        model, source = FssaiLicense, "license_data"
    elif prefix == "2":
        model, source = FssaiRegistration, "registration_data"
    else:
        return LookupResult(
            found=False,
            error="Unrecognized License/Registration number prefix (expected to start with 1 or 2).",
        )

    row = db.session.get(model, license_no)  # PK lookup; identity-map cached

    if not row:
        return LookupResult(found=False, error="License/Registration number not found.")

    return LookupResult(
        found=True,
        error=None,
        data={
            "companyName": row.company_name,
            "fullAddress": row.full_address,
            "expiryDate": row.expiry_date,
            "source": source,
        },
    )


# --------------------------------------------------------------------------- #
# lookup_ce — KMC Trade License lookup with rate limiting                      #
# --------------------------------------------------------------------------- #


def lookup_ce(
    license_no: str,
    *,
    rate_limiter: Optional[RateLimiter] = None,
    http_client: Optional[DefaultKmcHttpClient] = None,
) -> LookupResult:
    """Fetches Trade License details from the KMC portal.

    Separated from the original god-function into four single-responsple
    concerns (depth 1→4):

    * rate-limiting  → :class:`RateLimiter` adapter (injectable)
    * HTTP transport  → :class:`DefaultKmcHttpClient` (injectable)
    * JSON repair     → :func:`repair_kmc_json` (pure)
    * response shaping→ local helper

    The ``rate_limiter`` and ``http_client`` kwargs are injection points:
    tests (and future transports) may supply stubs.  In production the
    defaults use file-lock coordination + TLS-verified ``httpx``.

    Returns a :class:`LookupResult`.  External failures (network errors)
    that previously surfaced as raised exceptions are now captured as
    ``LookupResult.error`` — callers no longer need ``try/except``.
    """
    if not license_no:
        return LookupResult(found=False, error="License number is required.")

    limiter = rate_limiter or RateLimiter()
    client = http_client or DefaultKmcHttpClient()

    try:
        limiter.acquire()
        client.warm()
        resp = client.post(
            _KMC_SEARCH_ACTION,
            data={"searchLicenseNo": license_no},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": _KMC_TRADE_LICENSE_JSP,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )

        data = repair_kmc_json(resp.text)
        if data is None:
            return LookupResult(
                found=False,
                error="KMC portal returned malformed JSON.",
            )

        if not data.get("success"):
            return LookupResult(found=False, error=None)  # not found, no error

        try:
            rows = data["licenseNo"][0]
            identity = rows[0]
        except (KeyError, IndexError):
            return LookupResult(found=False, error="KMC response structure unexpected.")

        fee_heads = [
            {"section": r.get("sectionCode"), "amount": r.get("demandAmount")}
            for r in rows
        ]
        return LookupResult(
            found=True,
            error=None,
            data={
                "identity": identity,
                "fee_heads": fee_heads,
                "is_closed": bool(identity.get("licClosingDate")),
            },
        )
    except httpx.HTTPError as e:
        logger.warning("KMC portal request failed: %s", e)
        return LookupResult(
            found=False,
            error=f"KMC lookup failed: {e}",
        )
    finally:
        if http_client is None:
            # We created it; close it.
            client.close()
        else:
            # Caller injected — leave their connection alive.
            pass
