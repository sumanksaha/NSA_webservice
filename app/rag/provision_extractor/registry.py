"""Provision family registry — title → gold family token, and id construction.

Single source of truth mapping an Act / instrument title onto the benchmark's
family prefix, following ``evaluation/resolution.py`` ``FamilyMap`` /
``_FAMILY_ALIASES`` conventions so emitted ``provision_id`` values satisfy the
gold grammar parsed by ``evaluation/benchmark.py::_section_from_id``.

The alias table is duplicated here (rather than importing ``evaluation``) to
keep the ingestion-time provisioning path free of a dependency on the
evaluation package.
"""

from __future__ import annotations

import re

#: Family prefix → distinctive title substrings.  Longest alias wins, so more
#: specific instruments take precedence over their parent Act.
_FAMILY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fssai", ("food safety and standards",)),
    ("epa", ("environment protection act",)),
    ("water_act", ("water prevention and control of pollution",)),
    ("air_act", ("air prevention and control of pollution",)),
    ("pwm_rules", ("plastic waste management rules 2016", "plastic waste management")),
    ("swm_rules", ("solid waste management",)),
    ("kmc", ("kolkata municipal corporation",)),
    ("wbpt", ("west bengal premises tenancy",)),
    ("contract", ("indian contract act",)),
    ("sog", ("sale of goods act",)),
    ("partnership", ("indian partnership act",)),
    ("comp", ("companies act 2013",)),
    ("limitation", ("limitation act",)),
    ("cpa", ("consumer protection act",)),
    ("srf", ("specific relief act",)),
    ("pcra", ("prevention of cruelty to animals",)),
    ("wbmo", ("west bengal meat order", "meat order")),
    ("bns", ("bharatiya nyaya sanhita",)),
)

#: Alias list flattened and sorted most-specific-first for containment matching.
_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((family, alias) for family, aliases in _FAMILY_ALIASES for alias in aliases),
        key=lambda pair: len(pair[1]),
        reverse=True,
    ),
)

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def normalize_title(name: str | None) -> str:
    """Lower-case, strip leading articles, collapse punctuation to spaces."""
    text = re.sub(r"^(?:the|an|a)\s+", "", str(name or "").strip(), flags=re.IGNORECASE)
    return _PUNCT_RE.sub(" ", text.lower()).strip()


def slug_token(name: str | None) -> str:
    """Fallback family token when no alias matches (first meaningful word)."""
    normalized = normalize_title(name)
    return normalized.split(" ", 1)[0] if normalized else ""


def family_for_title(title: str | None, act_name: str | None = None) -> str | None:
    """Resolve the gold family token for an instrument title / act name.

    ``act_name`` takes precedence (it is the authoritative instrument stamp);
    the document ``title`` is the fallback.  Returns ``None`` when neither
    carries a recognised family — callers must not guess.
    """
    for candidate in (act_name, title):
        normalized = normalize_title(candidate)
        if not normalized:
            continue
        for family, alias in _ALIASES:
            if alias in normalized:
                return family
    return None


def family_token(title: str | None, act_name: str | None = None) -> str:
    """Family token for id construction, falling back to a title slug.

    Unlike :func:`family_for_title` this never returns ``None`` — it is used
    when a record must still receive *some* namespaced id.  Prefer
    :func:`family_for_title` when a fail-closed decision is required.
    """
    resolved = family_for_title(title, act_name)
    if resolved:
        return resolved
    return slug_token(act_name) or slug_token(title) or "unknown"


def provision_id(family: str, section: str, subsection: list[str] | None = None) -> str:
    """Build a gold-grammar provision id: ``<family>:s<section>[(<sub>)]``."""
    suffix = "".join(f"({sub})" for sub in (subsection or []) if sub)
    return f"{family}:s{section}{suffix}"
