"""FSSAI-registry premises resolution — the locality seam.

The one deep module behind "find the premises an FSO means by this free
text". Owns the FSSAI licence/registration lookup against Supabase
(``fssai_licenses`` / ``fssai_registrations``), the address-normalisation
and road-alias rules, and the merge/dedupe/order of candidates.

Why this exists (measured against the live registry, 2026-10-11): the same
road is spelled many ways (``N.S.C BOSE ROAD``, ``NSC BOSE ROAD``,
``NETAJI SUBHAS CHANDRA BOSE ROAD``) and a naive ``ILIKE '%BOSE ROAD%'``
both under-returns real premises and over-returns *different* streets
(``SARAT BOSE ROAD``, ``A.J.C BOSE ROAD``). This module expands a configured
alias set and labels the matched road so a caller (or an agent) cannot
mistake one street for another.

Engine: a Supabase Postgres pooler DSN (``SUPABASE_DB_URL``) read through the
config seam (``cfg.supabase_db_url``). The engine is built once per process
and reused — a fresh ``create_engine`` per request is what the old inline
route did. No result is ever cached across a request; the bi-monthly bulk
load owns row freshness.

Callers get a :class:`ResolveResult` whose invariant mirrors
:data:`app.utils.lookup.LookupResult` — a clean "found nothing" (``found``
False, ``error`` None) is distinguishable from a failure (``error`` set).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text

from app.shared.config import cfg

# ---------------------------------------------------------------------------
# Road-name aliases
# ---------------------------------------------------------------------------
#: Free-text spellings that resolve to one canonical road. Keyed by a short
#: stable id used in the UI/tests; each entry lists literal substrings that
#: identify the same physical road. Matching is case-insensitive after
#: normalisation. Extend this (data, not branching code) as new streets are
#: encountered — see docs/SCHEDULE_PLANNING_AGENT_SPEC.md §5.6.
ROAD_ALIASES: dict[str, tuple[str, ...]] = {
    "nsc_bose": (
        "NSC",
        "N S C",
        "N.S.C",
        "NETAJI SUBHAS CHANDRA",
        "SUBHAS CHANDRA",
    ),
}

#: Roads that are frequently confused with an aliased road. They are matched
#: and returned as a separate, explicitly labelled group — never merged into
#: the canonical group — so a caller cannot act on the wrong street.
CONFUSABLE_ROADS: dict[str, tuple[str, ...]] = {
    "sarat_bose": ("SARAT BOSE ROAD",),
    "ajc_bose": ("AJ C BOSE ROAD", "A.J.C BOSE ROAD"),
    "sudhir_bose": ("DOCTOR SUDHIR BOSE ROAD",),
}

#: Words that join a business name to its locality in free text:
#: "Balaram Mullick **in** NSC Bose Road". Splitting on these lets a query
#: carry two independent filters instead of one impossible literal phrase.
_CONNECTIVES = frozenset({
    "IN", "AT", "NEAR", "ON", "OF", "OPPOSITE", "ADJACENT", "BESIDE",
    "NEXT", "TO", "AND", "&",
})

#: Generic category words, not business names. "restaurant in NSC Bose Road"
#: means *anything* on that road; treating "restaurant" as a name filter
#: would wrongly exclude most of it. Dropped before the name filter is built.
_GENERIC_DESCRIPTORS = frozenset({
    "RESTAURANT", "RESTAURANTS", "HOTEL", "HOTELS", "SHOP", "SHOPS",
    "STORE", "STORES", "BAKERY", "SWEETS", "SWEET", "CAFE", "DHABA",
    "EATERY", "PREMISES", "PREMISE", "OUTLET", "BRANCH", "FIRM", "COMPANY",
    "CO", "AND", "CO.", "M/S", "UNIT", "OF",
})

def split_query(query: str) -> tuple[str, str]:
    """Split *"Balaram Mullick in NSC Bose Road"* into name and locality parts.

    Splits on the first connective that has content on both sides, so the
    locality can be matched against ``full_address`` while the name is matched
    against ``company_name`` — two independent filters instead of one literal
    phrase that appears in neither column.

    Returns ``(name_part, locality_part)``; either may be empty. A name part
    made up entirely of :data:`_GENERIC_DESCRIPTORS` is dropped, so
    *"restaurant in NSC Bose Road"* degrades gracefully to a locality-only
    search instead of excluding every business that isn't literally called
    "restaurant".
    """
    normalized = normalize_address(query)
    if not normalized:
        return "", ""
    tokens = normalized.split(" ")
    for idx, token in enumerate(tokens):
        if token in _CONNECTIVES and idx > 0 and idx < len(tokens) - 1:
            left = " ".join(tokens[:idx])
            right = " ".join(tokens[idx + 1:])
            # Drop generic category words; keep real business-name tokens.
            name_tokens = [t for t in left.split(" ") if t and t not in _GENERIC_DESCRIPTORS]
            return " ".join(name_tokens), right
    return normalized, ""


#: Tokens that mark a phrase as a place rather than a business name. Used
#: when a query has no connective ("NSC Bose Road" alone).
_LOCALITY_SUFFIXES = frozenset({
    "ROAD", "RD", "STREET", "STR", "SARANI", "SARANEE", "AVENUE", "LANE",
    "PLACE", "NAGAR", "PARK", "SECTOR", "BLOCK", "COLONY", "MARKET", "BYE",
    "ROW", "TOWER", "PLAZA", "MALL", "COMPLEX",
})


def looks_like_locality(part: str) -> bool:
    """True when a connective-free query reads as a place, not a business name.

    ``"NSC Bose Road"`` is a road, not a company — without this it would be
    token-matched against ``company_name`` and return nothing.
    """
    normalized = normalize_address(part)
    if not normalized:
        return False
    if _road_group_for_query(normalized):
        return True
    tokens = normalized.split(" ")
    # Any token suffices: "Salt Lake Sector V" places SECTOR mid-phrase.
    return any(tok in _LOCALITY_SUFFIXES for tok in tokens)


def _name_filter_clauses(name_part: str) -> list[str]:
    """Token-wise ILIKE clauses a company name must satisfy.

    Tokenising rather than matching the whole phrase handles registry entries
    like ``BALARAM MULLICK & RADHARAMAN MULLICK LLP`` for the query
    "balaram mullick", and is indifferent to word order.
    """
    tokens = [t for t in name_part.split(" ") if t]
    if not tokens:
        return []
    if len(tokens) > 4:
        return [name_part]  # long names match as a phrase
    return tokens


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DOT_RE = re.compile(r"\.")
_WS_RE = re.compile(r"\s+")
_LIKE_SPECIALS = ("\\", "%", "_")
#: The registry carries literal carriage-return artifacts (not real control
#: bytes) from an upstream export, e.g. "...KOLKATA-700040,_x000d_".
_X000D_RE = re.compile(r"_x000d_", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoadGroup:
    """Which road family a matched address belongs to.

    Replaces a bare string with a parsed ``"confusable:<id>"`` prefix: the
    distinction between "the road you asked for" and "a similarly-named road
    somewhere else" is a domain concept, and the UI must not have to re-parse
    it to render the two groups separately.
    """

    id: str = "other"  # a ROAD_ALIASES / CONFUSABLE_ROADS key, or "other"
    is_confusable: bool = False  # a look-alike road, never the queried one

    @classmethod
    def parse(cls, label: str) -> RoadGroup:
        """Build from the stored label (``"nsc_bose"`` / ``"confusable:sarat_bose"``)."""
        if label.startswith("confusable:"):
            return cls(label.split(":", 1)[1], is_confusable=True)
        return cls(label or "other")


@dataclass(frozen=True, slots=True)
class PremiseCandidate:
    """One FSSAI registry row. Immutable; safe for a template or a prompt."""

    fssai_number: str
    company_name: str | None
    full_address: str | None
    expiry_date: str | None
    source_table: str
    road: RoadGroup = RoadGroup()


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """Normalized return type — mirrors ``app.utils.lookup.LookupResult``.

    Invariant: ``found is False and error is None`` means the lookup completed
    without error and simply matched nothing. ``error is not None`` means the
    lookup failed; ``candidates`` is then always empty.
    """

    found: bool
    error: str | None = None
    candidates: tuple[PremiseCandidate, ...] = field(default_factory=tuple)
    road_group: str | None = None  # the matched alias id, when an alias matched
    name_filter: str = ""  # business-name tokens the query was split into
    locality_filter: str = ""  # the locality the query was split into

    def __bool__(self) -> bool:
        return self.found


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_address(value: str | None) -> str:
    """Uppercase, strip control chars, drop dots, collapse whitespace.

    Collapses the registry's punctuation variants into one comparable form
    (``N. S. C  BOSE ROAD`` -> ``N S C BOSE ROAD``) so a road token matches
    regardless of how it was typed. Spec §5.6 steps N1–N3.
    """
    if not value:
        return ""
    text_ = value.upper()
    text_ = _X000D_RE.sub(" ", text_)
    text_ = _CTRL_RE.sub(" ", text_)
    text_ = _DOT_RE.sub(" ", text_)
    text_ = _WS_RE.sub(" ", text_)
    return text_.strip()


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so user text can't widen the scan."""
    out = value
    for ch in _LIKE_SPECIALS:
        out = out.replace(ch, "\\" + ch)
    return out


def _classify_road(normalized_address: str) -> RoadGroup:
    """Return the road family the address belongs to (canonical or look-alike)."""
    for alias_id, needles in ROAD_ALIASES.items():
        if any(n in normalized_address for n in needles):
            return RoadGroup(alias_id)
    for cid, needles in CONFUSABLE_ROADS.items():
        if any(n in normalized_address for n in needles):
            return RoadGroup(cid, is_confusable=True)
    return RoadGroup()


# ---------------------------------------------------------------------------
# Engine lifecycle (process-wide, reused — never per-request)
# ---------------------------------------------------------------------------

_engine = None


def _get_engine():
    """Return the cached Supabase engine, or None when not configured."""
    global _engine
    if _engine is not None:
        return _engine
    dsn = cfg.supabase_db_url
    if not dsn:
        return None
    _engine = create_engine(
        dsn,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_recycle=280,
        connect_args={"sslmode": "require"},
    )
    return _engine


def reset_engine_cache() -> None:
    """Drop the cached engine (tests / config reload)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

#: Tables are hardcoded (identifier injection impossible); only the value is
#: bound. Both branches return the id under ``fssai_number`` and tag their
#: source table.
_SQL_LICENSE = """
SELECT license_no       AS fssai_number, company_name, full_address, expiry_date,
       'fssai_licenses' AS source_table
FROM fssai_licenses
WHERE company_name ILIKE :q OR full_address ILIKE :q
ORDER BY company_name
LIMIT :lim
"""

_SQL_REGISTRATION = """
SELECT registration_no AS fssai_number, company_name, full_address, expiry_date,
       'fssai_registrations' AS source_table
FROM fssai_registrations
WHERE company_name ILIKE :q OR full_address ILIKE :q
ORDER BY company_name
LIMIT :lim
"""


def _road_group_for_query(query: str) -> str | None:
    """If the free text names a known canonical road, return its alias id."""
    normalized = normalize_address(query)
    for alias_id, needles in ROAD_ALIASES.items():
        if any(n in normalized for n in needles):
            return alias_id
    return None


def search_by_fssai(number: str) -> ResolveResult:
    """Exact lookup by FSSAI number.

    Dispatches by first character (``1…`` → licences, ``2…`` → registrations),
    mirroring ``app.utils.lookup.lookup_fssai``. The inversion is historical
    and intentional — do not "fix" it (app/models/lookup.py:16-20).
    """
    engine = _get_engine()
    if engine is None:
        return ResolveResult(False, error="Supabase is not configured.")
    number = (number or "").strip()
    if not number:
        return ResolveResult(False)

    # Dispatch table first, then the other. ``resolve`` searches *both* tables
    # regardless of prefix, so a candidate it returns can live in the table the
    # prefix rule alone would not pick; without the fallback a selection made
    # from the UI silently fails to resolve. The lookup is by primary key
    # either way, so the fallback cannot return a wrong row.
    if number[0] == "1":
        order = (
            ("fssai_licenses", "license_no"),
            ("fssai_registrations", "registration_no"),
        )
    else:
        order = (
            ("fssai_registrations", "registration_no"),
            ("fssai_licenses", "license_no"),
        )
    try:
        with engine.connect() as conn:
            for table, col in order:
                row = conn.execute(
                    # table/col come from the hardcoded `order` tuple above —
                    # never from user input; only :n is bound.
                    text(f"SELECT company_name, full_address, expiry_date FROM {table} WHERE {col} = :n"),  # noqa: S608
                    {"n": number},
                ).mappings().first()
                if row is not None:
                    return ResolveResult(
                        True,
                        candidates=(
                            PremiseCandidate(
                                fssai_number=number,
                                company_name=row["company_name"],
                                full_address=row["full_address"],
                                expiry_date=row["expiry_date"],
                                source_table=table,
                            ),
                        ),
                    )
    except Exception as exc:
        return ResolveResult(False, error=f"Lookup failed: {exc}")
    return ResolveResult(False)


def resolve(query: str, *, limit: int = 50) -> ResolveResult:
    """Resolve free text to premise candidates.

    Matching is alias-aware: when the query names a known road, every literal
    spelling of that road is OR-ed together so real premises on that road are
    found regardless of how the registry punctuated them. Look-alike roads
    (see :data:`CONFUSABLE_ROADS`) are returned but tagged so the UI can keep
    them visually separate — they are never merged into the canonical group.
    """
    query = (query or "").strip()
    if not query:
        return ResolveResult(False)

    engine = _get_engine()
    if engine is None:
        return ResolveResult(False, error="Supabase is not configured.")

    name_part, locality_part = split_query(query)
    # A connective-free query that reads as a place is a locality search.
    if not locality_part and name_part and looks_like_locality(name_part):
        locality_part, name_part = name_part, ""
    name_tokens = _name_filter_clauses(name_part)

    params: dict[str, object] = {}
    where: str
    locality_enabled = cfg.premises_search_locality_enabled

    if locality_part and locality_enabled:
        # Locality branches: the typed locality plus, when it names a known
        # road, every literal spelling of that road.
        branches = [_escape_like(locality_part)]
        query_group = _road_group_for_query(locality_part)
        if query_group:
            for needle in ROAD_ALIASES[query_group]:
                branch = _escape_like(needle)
                if branch not in branches:
                    branches.append(branch)
        for i, branch in enumerate(branches):
            params[f"b{i}"] = f"%{branch}%"
        locality_clause = " OR ".join(f"full_address ILIKE :b{i}" for i in range(len(branches)))
        if name_tokens:
            # Two independent filters: the name AND the road. Never fall back
            # to road-only — that would answer "Balaram Mullick in NSC Bose
            # Road" with every business on that road.
            name_clause = " AND ".join(f"company_name ILIKE :n{i}" for i in range(len(name_tokens)))
            params.update({f"n{i}": f"%{_escape_like(tok)}%" for i, tok in enumerate(name_tokens)})
            where = f"({name_clause}) AND ({locality_clause})"
        else:
            where = " OR ".join(
                f"(company_name ILIKE :b{i} OR full_address ILIKE :b{i})" for i in range(len(branches))
            )
    elif name_tokens or not locality_enabled:
        # No usable locality — filter on the business name alone. This is also
        # the legacy behaviour when PREMISES_SEARCH_LOCALITY_ENABLED is off.
        query_group = None
        if name_tokens:
            where = " AND ".join(f"company_name ILIKE :n{i}" for i in range(len(name_tokens)))
            params.update({f"n{i}": f"%{_escape_like(tok)}%" for i, tok in enumerate(name_tokens)})
        else:
            where = "company_name ILIKE :b0"
            params["b0"] = f"%{_escape_like(query)}%"
    else:
        # Nothing structured to filter on — loose match, alias-aware.
        branches = [_escape_like(query)]
        query_group = _road_group_for_query(query)
        if query_group:
            for needle in ROAD_ALIASES[query_group]:
                branch = _escape_like(needle)
                if branch not in branches:
                    branches.append(branch)
        for i, branch in enumerate(branches):
            params[f"b{i}"] = f"%{branch}%"
        where = " OR ".join(
            f"(company_name ILIKE :b{i} OR full_address ILIKE :b{i})" for i in range(len(branches))
        )

    params["lim"] = limit
    sql_license = _SQL_LICENSE.replace(
        "WHERE company_name ILIKE :q OR full_address ILIKE :q", f"WHERE {where}"
    )
    sql_registration = _SQL_REGISTRATION.replace(
        "WHERE company_name ILIKE :q OR full_address ILIKE :q", f"WHERE {where}"
    )

    rows: list[dict] = []
    try:
        with engine.connect() as conn:
            for sql in (sql_license, sql_registration):
                rows.extend(dict(r) for r in conn.execute(text(sql), params).mappings())
    except Exception as exc:
        return ResolveResult(False, error=f"Lookup failed: {exc}")

    # Merge + dedupe on (number, normalized address); classify road group.
    seen: set[tuple[str, str]] = set()
    candidates: list[PremiseCandidate] = []
    for row in rows:
        key = (row["fssai_number"], normalize_address(row["full_address"]))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            PremiseCandidate(
                fssai_number=row["fssai_number"],
                company_name=row["company_name"],
                full_address=row["full_address"],
                expiry_date=row["expiry_date"],
                source_table=row["source_table"],
                road=_classify_road(normalize_address(row["full_address"])),
            )
        )

    # M6 ordering: exact company-name match first, then a name that starts with
    # the query, then address-only matches; a canonical-road group always
    # outranks a confusable look-alike road.
    probe = normalize_address(name_part) if name_tokens else ""

    def rank(c: PremiseCandidate) -> tuple:
        is_confusable = 1 if query_group and c.road.id != query_group else 0
        name = normalize_address(c.company_name)
        if not probe or name == probe:
            name_rank = 0
        elif name.startswith(probe):
            name_rank = 1
        elif probe in name:
            name_rank = 2
        else:
            name_rank = 3  # address-only match
        return (is_confusable, name_rank, name)

    if query_group or probe:
        candidates.sort(key=rank)

    return ResolveResult(
        found=bool(candidates),
        candidates=tuple(candidates[:limit]),
        road_group=query_group,
        name_filter=name_part if name_tokens else "",
        locality_filter=locality_part,
    )
