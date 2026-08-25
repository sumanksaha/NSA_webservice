"""FSSAI lookup reference-data models.

Static-schema reference data backing ``lookup_fssai()`` in
``app/utils/lookup.py``, migrated out of the git-tracked SQLite files
``db/license_data.db`` / ``db/registration_data.db``
(see ``docs/FSSAI_LOOKUP_POSTGRES_PLAN.md`` and
``docs/FSSAI_LOOKUP_POSTGRES_RESEARCH.md``).

Dispatch contract preserved from the SQLite implementation:

- FSSAI numbers starting with ``"1"`` -> :class:`FssaiLicense`
  (table ``fssai_licenses``)
- FSSAI numbers starting with ``"2"`` -> :class:`FssaiRegistration`
  (table ``fssai_registrations``)

.. warning:: Naming inversion (historical, intentional): the SQLite
   docstring says numbers starting with ``"1"`` belong to
   *Registration-category* FBOs even though they resolve to the
   *license_records* table, and vice versa for ``"2"``.  The mapping is
   mechanical, not semantic — do not "fix" it by swapping the tables.

All values are stored as ``Text`` to match the SQLite sources exactly;
in particular ``expiry_date`` holds ``DD-MM-YYYY`` strings that callers
pass through verbatim (no date parsing or comparison anywhere), so a
typed ``Date`` column would change the API response contract.
"""

from __future__ import annotations

from app.extensions import db


class FssaiLicense(db.Model):
    """FSSAI license record (SQLite source: ``license_records``).

    Resolved by exact primary-key match on ``license_no`` for FSSAI
    numbers starting with ``"1"``.  Rows arrive via the bi-monthly bulk
    upsert refresh (``scripts/load_fssai_lookup.py``); the application
    never writes to this table at request time.
    """

    __tablename__ = "fssai_licenses"

    license_no = db.Column(db.Text, primary_key=True)
    company_name = db.Column(db.Text, nullable=True)
    full_address = db.Column(db.Text, nullable=True)
    # DD-MM-YYYY string, pass-through only — see module docstring.
    expiry_date = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<FssaiLicense {self.license_no} company={self.company_name!r}>"


class FssaiRegistration(db.Model):
    """FSSAI registration record (SQLite source: ``registration_records``).

    Resolved by exact primary-key match on ``registration_no`` for FSSAI
    numbers starting with ``"2"``.  Same refresh semantics as
    :class:`FssaiLicense`.
    """

    __tablename__ = "fssai_registrations"

    registration_no = db.Column(db.Text, primary_key=True)
    company_name = db.Column(db.Text, nullable=True)
    full_address = db.Column(db.Text, nullable=True)
    # DD-MM-YYYY string, pass-through only — see module docstring.
    expiry_date = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:
        return f"<FssaiRegistration {self.registration_no} company={self.company_name!r}>"
