"""Chunk-side regulatory provision/document-role metadata — derived at query time.

Task spec §3: every chunk should carry ``commodity`` / ``document_role`` /
``provision_type`` / ``table`` context.  The indexed payloads (12,838 fssai
points) do not carry these fields, and re-ingestion is out of scope, so this
module **derives** them from what is already known about a chunk — its text,
``clause_number``, ``section_number``, ``document_type``, ``document_title``
and ``hierarchy_level``.

Rules:

* **Never fabricate** — a field that cannot be read from the chunk is the
  literal string ``"unknown"``, not a guess (task spec §3).
* **Deterministic** — same chunk in, same metadata out; results are memoized
  per ``chunk_id`` for the process lifetime (retrieval is LRU-cached anyway).
* **Reuses existing detectors** — provision/operative language detection comes
  from ``legal_identity.detect_provision_type`` (already production-tested);
  this module only adds the food-standard-specific roles on top.

The derived dict is the **metadata schema** (task spec §23.4)::

    {
      "commodity": "cumin",            # or "unknown"
      "entity": "cumin",               # alias of commodity (schema parity)
      "document_role": "standard",     # role vocabulary below, or "general"
      "provision_type": "food_standard",
      "section": "2.9.8" | "unknown",
      "regulation": "Food Additives Regulations-4" | "unknown",
      "act": "Food Safety and Standards Act, 2006" | "unknown",
      "table": "requirements table" | "unknown",
      "parent_chunk_id": ... | "unknown",
      "document_id": ... | "unknown",
      "page": "unknown",
      "source": document_uri | "unknown",
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "DOCUMENT_ROLES",
    "ProvisionMetadata",
    "derive_provision_metadata",
    "is_definition_chunk",
    "is_standard_chunk",
]

#: Document/provision role vocabulary (task spec §3).
DOCUMENT_ROLES: frozenset[str] = frozenset(
    {
        "definition",
        "standard",
        "requirement",
        "limit",
        "prohibition",
        "procedure",
        "sampling",
        "licensing",
        "penalty",
        "scope",
        "explanation",
        "general",
    },
)

#: Clause-number style of the FSSAI commodity standards (e.g. "2.9.8 Cumin").
#: A leading dotted clause number is the anchor for standard-table chunks.
_CLAUSE_LEAD_RE = re.compile(r"^\s*(\d{1,2}\.\d{1,3}(?:\.\d{1,3})?)\s*[:.]?\s*")

#: Definition shapes — "X means …" / "X means the powder obtained by …".
_MEANS_RE = re.compile(r"\bmeans\b", re.IGNORECASE)

#: Standard/requirement language — the regulatory provision words of §8.
_STANDARD_LANGUAGE_RE = re.compile(
    r"\bshall\s+conform\b|\bconform\s+to\b|\brequirements?\b|\bshall\s+comply\b|"
    r"\bshall\s+meet\b|\bstandards?\b|\bshall\s+not\s+exceed\b|\bnot\s+(?:more|less)\s+than\b|"
    r"\bmaximum\b|\bminimum\b|\bper\s+cent(?:um)?\s+by\s+weight\b|\bpercent(?:age)?\s+by\s+weight\b",
    re.IGNORECASE,
)

#: Parameter-row markers — "(i) Moisture Not more than 10.0 percent by weight".
_TABLE_ROW_RE = re.compile(r"\((?:[ivx]+|\d+)\)\s+\w", re.IGNORECASE)
_MEASUREMENT_RE = re.compile(
    r"\b(?:not\s+more\s+than|not\s+less\s+than|maximum|minimum|per\s+cent|percent|mg/kg|ppm|"
    r"per\s+cent\s+by\s+weight)\b",
    re.IGNORECASE,
)

#: Commodity-name extraction from a clause heading:
#: "2.9.8: Cumin (Zeera, Kalonji) 1. Cumin (Safed Zeera) whole means …"
#: → the text between the clause number and the first sentence marker.
_HEADING_TAIL_RE = re.compile(
    r"^(?P<tail>[A-Z][A-Za-z ()/&,\-']{2,60}?)(?=\s+\d\s*\.|\s+means\b|\s+shall\b|$)",
)

#: Words that terminate a commodity phrase (relative clauses / generic
#: continuations — "cumin shall conform" must not become "cumin shall").
_NOT_PHRASE_WORDS: frozenset[str] = frozenset(
    {
        "shall",
        "means",
        "whole",
        "powder",
        "and",
        "or",
        "the",
        "of",
        "in",
        "for",
        "with",
        "are",
        "is",
    },
)

#: Known Indian-commodity words that may appear inside a clause heading.
_KNOWN_COMMODITIES: tuple[str, ...] = (
    "cumin",
    "coriander",
    "cinnamon",
    "cardamom",
    "turmeric",
    "chilli",
    "pepper",
    "clove",
    "ginger",
    "saffron",
    "nutmeg",
    "mace",
    "fenugreek",
    "fennel",
    "celery",
    "aniseed",
    "caraway",
    "dill",
    "ajwan",
    "mustard",
    "curry powder",
    "masala",
    "asafoetida",
    "butter",
    "cheese",
    "ghee",
    "margarine",
    "honey",
    "jam",
    "jelly",
    "marmalade",
    "fruit squash",
    "beverage",
    "ice cream",
    "milk",
    "dahi",
    "paneer",
    "flour",
    "maida",
    "suji",
    "besan",
    "rice",
    "wheat",
    "sugar",
    "jaggery",
    "salt",
    "edible oil",
    "vanaspati",
    "bakery",
    "bread",
    "biscuit",
    "cake",
    "confectionery",
    "cocoa",
    "coffee",
    "tea",
    "meat",
    "fish",
    "egg",
    "poultry",
)


def _load_harvested_commodities() -> tuple[str, ...]:
    """Commodity names harvested from the corpus's own clause headings.

    Reads ``commodity_vocabulary.json`` (emitted by
    ``evaluation/harvest_commodity_vocabulary.py`` — one-shot, deterministic
    sweep of every clause-lead heading across all indexed collections).
    Only the *commodity names* list is consumed: harvested synonym
    candidates are deliberately NOT auto-wired (a wrong synonym such as
    ``"edible" -> "catechu"`` would hijack every query containing the
    word; review + promote them by hand instead).

    Missing or malformed file -> empty tuple: the hand-curated base list
    above remains the complete vocabulary, exactly as before the harvest
    existed.
    """
    path = Path(__file__).resolve().parent / "commodity_vocabulary.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        names = data.get("commodities") or []
        return tuple(str(n).lower() for n in names if str(n).strip())
    except (OSError, ValueError, AttributeError):
        return ()


#: Full vocabulary = hand-curated base + corpus-harvested names (union,
#: order-stable, deduped).  Harvested names make entity extraction and the
#: chunk-side commodity derivation cover every product family indexed in
#: the corpus (dairy 2.1, beverages 2.3, bakery 2.4, spices 2.9, ...).
_KNOWN_COMMODITIES: tuple[str, ...] = tuple(
    dict.fromkeys((*_KNOWN_COMMODITIES, *_load_harvested_commodities())),
)

#: Form words — when they follow a commodity name the phrase is the SAME
#: commodity in another physical form ("Cumin whole" / "Cumin powder" share
#: clause 2.9.8's identity).
_FORM_WORDS: frozenset[str] = frozenset(
    {"whole", "powder", "ground", "dried", "seeds", "seed", "fresh", "frozen", "extract", "oleoresin"},
)

#: Modifier words — when they follow a commodity name the phrase is a
#: DIFFERENT commodity ("Cumin Black" / Kalonji has its own sub-standards).
_COMMODITY_MODIFIERS: frozenset[str] = frozenset({"black", "white", "green", "red", "brown", "yellow"})

#: Product-type words — "Ginger Cocktail" / "Ginger Paste" are distinct
#: products with their own clauses, NOT the commodity "ginger".
_PRODUCT_WORDS: frozenset[str] = frozenset(
    {"cocktail", "beer", "ale", "paste", "sauce", "juice", "drink", "beverage", "mix", "squash", "bar"},
)

#: Compound heads — when one of these immediately PRECEDES a commodity word
#: the phrase is a distinct product named head-first ("Pan Masala",
#: "garam masala", "meat masala"), not the commodity itself.  Mirrors the
#: next-word rule below for left-branching compounds (ablation iteration 7:
#: the clause 3.1.3 gazette blob matched "masala" only via "Pan Masala
#: 8000 ppm" and beat the MIXED MASALA standard clause).
_COMPOUND_HEADS: frozenset[str] = frozenset(
    {"pan", "garam", "chai", "chaat", "chat", "sambhar", "sambar", "tandoori", "fruit", "meat", "chicken", "mutton", "fish", "egg", "vegetable"},
)


def commodity_agrees(derived: str, entity: str) -> bool:
    """Whether a chunk's derived commodity and a query entity are the SAME
    commodity — bidirectional, guarded by the compound rules.

    Needed once the vocabulary is harvested from the corpus: the query side
    may carry the full harvested name ("mixed masala") while the chunk-side
    heading derives the shorter head ("masala"), or the reverse.  Plain
    single-direction :func:`commodity_phrase_match` misses one direction and
    plain substring matching would wrongly agree on distinct commodities
    ("cumin" vs "Cumin Black" a.k.a. kalonji, "masala" vs "Pan Masala",
    "ginger" vs "Ginger Cocktail").

    Agreement = equality, or containment in either direction where the
    residual words carry no distinct-commodity marker (product words,
    colour modifiers, compound heads, or other known commodities):

        ("mixed masala", "masala")          -> True   (family/clause pair)
        ("masala", "mixed masala")          -> True
        ("pan masala", "masala")            -> False  (compound head)
        ("cumin", "cumin black")            -> False  (colour modifier)
        ("ginger", "ginger cocktail")       -> False  (product word)
        ("masala", "masala milk")           -> False  (other commodity)
        ("wine", "white wine")              -> False  (colour modifier)
    """
    d = (derived or "").lower().strip()
    e = (entity or "").lower().strip()
    if not d or not e:
        return False
    if d == e:
        return True
    if e in d:
        residual = d.replace(e, " ").split()
    elif d in e:
        residual = e.replace(d, " ").split()
    else:
        return False
    known = set(_KNOWN_COMMODITIES)
    for w in residual:
        if w in _PRODUCT_WORDS or w in _COMMODITY_MODIFIERS or w in _COMPOUND_HEADS or w in known:
            return False
    return True


def commodity_phrase_match(text: str, entity: str) -> bool:
    """Form-family-aware entity mention test (ablation iteration 4).

    True when *entity* appears as a commodity phrase in *text*::

        "Cumin (Safed Zeera) whole means …"   → cumin      True
        "cumin powder shall conform …"        → cumin      True
        "2.3.22 Ginger Cocktail: …"           → ginger     False
        "Cumin Black (Kalonji) whole means …" → cumin      False
        "masala milk" / "masala bread"        → masala     False

    Deterministic: word-boundary hit, then the NEXT word decides —
    form words ("whole", "powder") keep the match; compound continuations
    (products, colour modifiers, other commodities) mark the occurrence as a
    DIFFERENT commodity and the scan continues to the next occurrence.
    """
    e = (entity or "").lower().strip()
    t = (text or "").lower()
    if not e or not t:
        return False
    known = set(_KNOWN_COMMODITIES)
    for m in re.finditer(rf"\b{re.escape(e)}\b", t):
        # Left-branching compound check: a compound head immediately before
        # the mention ("Pan Masala", "garam masala") names a DIFFERENT
        # product — the occurrence is not this entity's bare mention.
        head = t[: m.start()].rstrip().rsplit(" ", 1)[-1] if m.start() else ""
        head = re.sub(r"[^a-z]", "", head)
        if head and head in _COMPOUND_HEADS:
            continue
        tail = t[m.end():].lstrip()
        nxt = re.match(r"[a-z]+", tail)
        word = nxt.group(0) if nxt else ""
        if not word:
            return True  # end of text / punctuation — bare mention
        if word in _FORM_WORDS:
            return True
        if word in _PRODUCT_WORDS or word in _COMMODITY_MODIFIERS or word in known:
            continue  # compound product ("masala milk") — not this entity
        return True
    return False


#: Role markers for the non-standard documents (licensing/sampling/penalty…).
_ROLE_MARKERS: tuple[tuple[str, tuple[Any, ...]], ...] = (
    ("sampling", (re.compile(r"\bsampl(?:e|es|ing)\b", re.IGNORECASE), "sealed", "divided into")),
    ("licensing", (re.compile(r"\blicen[cs]e\b|\bregistration\b", re.IGNORECASE),)),
    ("penalty", (re.compile(r"\bpenalt(?:y|ies)\b|\bpunish\w*\b|\bfine\b|\bimprisonment\b", re.IGNORECASE),)),
    ("prohibition", (re.compile(r"\bprohibit\w*\b|\bno\s+person\s+shall\b", re.IGNORECASE),)),
    ("procedure", (re.compile(r"\bprocedure\b|\bmanner\s+of\b", re.IGNORECASE),)),
    ("scope", (re.compile(r"\bapplies\s+to\b|\bextend\w*\s+to\b", re.IGNORECASE),)),
)


class ProvisionMetadata(dict):
    """Derived chunk metadata (dict subclass for JSON-serialisability)."""


def _chunk_field(chunk: Any, name: str, default: Any = None) -> Any:
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


def _extract_commodity_from_heading(text: str) -> str | None:
    """Commodity name from a clause heading line, else ``None``.

    Compound-commodity aware (ablation iteration 3): "2.3.22 Ginger
    Cocktail: 1. …" is the clause for *ginger cocktail*, a different
    commodity from *ginger* — the compound phrase (commodity word + the
    following word, parenthetical synonyms stripped) is preferred over the
    bare word so the reranker can tell them apart.  Only explicit
    known-commodity words are trusted — never invented from prose.
    """
    # Compound pass: parenthetical synonyms ("(Zeera, Kalonji)") are not
    # part of the commodity phrase; a compound ("Ginger Cocktail", "Cumin
    # Black", "Masala Bread") forms ONLY when the next word is a recognised
    # product/modifier/commodity word — arbitrary prose continuations
    # ("milk may be denoted") must never fuse into a bogus commodity.
    stripped = re.sub(r"\([^)]*\)", " ", text)
    tokens = re.findall(r"[A-Za-z]+", stripped)
    known = set(_KNOWN_COMMODITIES)
    for i, tok in enumerate(tokens):
        tl = tok.lower()
        if tl in known:
            # Left-branching compound ("2.11.5 Pan Masala means …"): a
            # compound head immediately BEFORE the commodity word names a
            # distinct product — fuse it so the clause registers "pan masala",
            # not the bare head "masala" (ablation iteration 8: the bare
            # registration let the Pan Masala definition inherit a false
            # entity match for "mixed masala" queries).
            prev = tokens[i - 1].lower() if i else ""
            if prev and prev in _COMPOUND_HEADS:
                return f"{prev} {tl}"
            if i + 1 < len(tokens):
                nxt = tokens[i + 1].lower()
                if nxt != tl and (nxt in _PRODUCT_WORDS or nxt in _COMMODITY_MODIFIERS or nxt in known):
                    return f"{tl} {nxt}"
            return tl
    # Multi-word known commodities ("ice cream", "curry powder", …).
    low = text.lower()
    for commodity in _KNOWN_COMMODITIES:
        if " " in commodity and re.search(rf"\b{re.escape(commodity)}\b", low):
            return commodity
    return None


def derive_provision_metadata(chunk: Any) -> dict[str, Any]:
    """Derive the §3 metadata dict for one chunk.  Deterministic; no I/O.

    Accepts a ``RetrievedChunk``, a ``to_dict()`` dict, or any object with the
    payload fields.  Unknown values stay the literal ``"unknown"``.
    """
    text = str(_chunk_field(chunk, "text", "") or "")
    low = text.lower()
    clause = _chunk_field(chunk, "clause_number", None)
    section = _chunk_field(chunk, "section_number", None)
    document_title = str(_chunk_field(chunk, "document_title", "") or "")
    act_name = str(_chunk_field(chunk, "act_name", "") or "")
    document_uri = str(_chunk_field(chunk, "document_uri", "") or "")
    document_id = _chunk_field(chunk, "document_id", None)
    parent_chunk_id = _chunk_field(chunk, "parent_chunk_id", None)
    hierarchy_level = _chunk_field(chunk, "hierarchy_level", 0)

    # --- commodity / entity -------------------------------------------------
    commodity: str | None = None
    m = _CLAUSE_LEAD_RE.match(text)
    if m:
        # Heading chunk: look at the tail right after the clause number first
        tail_m = _HEADING_TAIL_RE.match(text[m.end():])
        if tail_m:
            commodity = _extract_commodity_from_heading(tail_m.group("tail"))
    if commodity is None:
        commodity = _extract_commodity_from_heading(text[:160])

    # --- document role ------------------------------------------------------
    role = _derive_role(low, text)

    # --- provision type (food_standard / definition / …) --------------------
    provision_type = _derive_provision_type(low, role, bool(clause))

    # --- table? -------------------------------------------------------------
    table = "unknown"
    row_count = len(_TABLE_ROW_RE.findall(text))
    if row_count >= 2 or (row_count >= 1 and _MEASUREMENT_RE.search(text)):
        table = f"requirements table ({row_count} rows)"

    return ProvisionMetadata(
        commodity=commodity or "unknown",
        entity=commodity or "unknown",
        document_role=role,
        provision_type=provision_type,
        section=str(clause or section) if (clause or section) else "unknown",
        regulation=document_title or "unknown",
        act=act_name or "unknown",
        table=table,
        parent_chunk_id=str(parent_chunk_id) if parent_chunk_id else "unknown",
        document_id=str(document_id) if document_id else "unknown",
        page="unknown",
        source=document_uri or "unknown",
        # Internal scoring signals (not part of the wire schema but harmless
        # and useful for the reranker; JSON-safe).
        _table_row_count=row_count,
        _has_measurement=bool(_MEASUREMENT_RE.search(text)),
        _hierarchy_level=int(hierarchy_level or 0),
    )


def _derive_role(low: str, raw: str) -> str:
    """Document-role from operative language.  definition wins over standard
    only for *pure* definition shapes; table fragments with measurements are
    ``limit``; the rest map through the marker table.
    """
    means = bool(_MEANS_RE.search(low))
    std = bool(_STANDARD_LANGUAGE_RE.search(low))
    rows = _TABLE_ROW_RE.findall(raw)

    # Pure definition: "X means …" with no requirement language.
    if means and not std and not rows:
        return "definition"
    # Standard provision: requirement language or a parameter table.
    if std or rows:
        return "standard"
    for role, markers in _ROLE_MARKERS:
        if any(m.search(raw) if isinstance(m, re.Pattern) else (m in low) for m in markers):
            return role
    if means:
        return "definition"
    return "general"


def _derive_provision_type(low: str, role: str, has_clause: bool) -> str:
    """Coarse provision_type for the §3 schema (distinct from operative
    provision_type).  Commodity-standard context: a standard/limit role inside
    a dotted-clause regulation document is a ``food_standard``.
    """
    if role == "standard":
        return "food_standard" if has_clause else "standard"
    if role == "definition":
        return "definition"
    if role in ("prohibition", "licensing", "penalty", "procedure", "sampling", "scope"):
        return role
    return "general"


# ---------------------------------------------------------------------------
# Fast boolean helpers (used by the reranker's two-stage gate)
# ---------------------------------------------------------------------------

def is_definition_chunk(chunk: Any) -> bool:
    """Whether this chunk is primarily a *definition* of a commodity.

    "Cumin … whole means the dried mature fruits of Cuminum Cyminum L." —
    the shape that historically outranks real standards.
    """
    text = str(_chunk_field(chunk, "text", "") or "")
    low = text.lower()
    if not _MEANS_RE.search(low):
        return False
    # Definition-shaped when "means" appears and the chunk is short/prose-like
    # (definitions are sentences, tables are row lists).
    rows = len(_TABLE_ROW_RE.findall(text))
    measurement = bool(_MEASUREMENT_RE.search(text))
    return not measurement and rows == 0


def is_standard_chunk(chunk: Any) -> bool:
    """Whether this chunk carries *standard/limit* substance.

    True for requirement language, measurement rows, or "shall conform"
    provisions — the evidence a food_standard query actually needs.
    """
    text = str(_chunk_field(chunk, "text", "") or "")
    if not text:
        return False
    if _STANDARD_LANGUAGE_RE.search(text):
        return True
    return bool(_TABLE_ROW_RE.findall(text)) and bool(_MEASUREMENT_RE.search(text))


# ---------------------------------------------------------------------------
# Memoization — retrieval is cached per query; metadata derivation is cheap
# but called for every candidate on every rerank, so memoize by chunk_id.
# ---------------------------------------------------------------------------

_METADATA_CACHE: dict[str, dict[str, Any]] = {}
_METADATA_CACHE_MAX = 4096


def derive_provision_metadata_cached(chunk: Any) -> dict[str, Any]:
    """Memoized :func:`derive_provision_metadata` keyed by chunk_id + text hash."""
    cid = str(_chunk_field(chunk, "chunk_id", "") or "")
    text = str(_chunk_field(chunk, "text", "") or "")
    key = f"{cid}:{hash(text)}"
    hit = _METADATA_CACHE.get(key)
    if hit is not None:
        return hit
    meta = derive_provision_metadata(chunk)
    if len(_METADATA_CACHE) >= _METADATA_CACHE_MAX:
        _METADATA_CACHE.clear()
    _METADATA_CACHE[key] = meta
    return meta
