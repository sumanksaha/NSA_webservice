"""License lookup adapter with retry and caching."""

from app.utils.lookup import LookupResult, lookup_ce, lookup_fssai


class LicenseLookupAdapter:
    """FSSAI/CE license lookup with retry and caching.

    Single interface: lookup(license_number, source) -> LookupResult.
    """

    def lookup(self, license_number: str, source: str = "fssai") -> LookupResult:
        """Look up a license by number and source."""
        if source == "fssai":
            return lookup_fssai(license_number)
        if source == "ce":
            return lookup_ce(license_number)
        return LookupResult(found=False, error=f"Unknown source: {source}")
