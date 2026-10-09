"""Food-commodity query understanding — ENTITY ≠ INFORMATION REQUEST.

The system-wide failure mode this module fixes::

    "What is cumin?"                        → intent = definition
    "What is the standard for cumin?"       → intent = food_standard
    "What is the moisture limit for cumin?" → intent = parameter_specific_standard

The entity ("cumin") identifies *what the user is asking about*; the intent
("standard"/"moisture") identifies *what relationship/provision the user
wants*.  Text-similarity retrieval alone returns the definition for both —
this module makes the distinction explicit so the retriever and reranker can
act on it.

Deterministic, rule-based (no LLM): pattern priority order is fixed, so the
same query always parses to the same value.  Failed entity extraction yields
``entity="unknown"`` — never a guessed commodity name.

This is a *satellite view* of the shared query-understanding seam: the
canonical ``QueryUnderstanding`` (``app/rag/retrieval/query_understanding.py``)
stays the single parse; ``FoodQueryUnderstanding.from_query()`` attaches the
food-commodity view alongside the legal view.  Nothing existing changes
behaviour when ``RAG_FOOD_INTENT_ENABLED`` is off.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FOOD_INTENTS",
    "PROVISION_TYPES",
    "FoodQueryUnderstanding",
    "commodity_synonyms",
    "detect_food_intent",
    "extract_entity",
    "extract_parameters",
]


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: Supported intents (task spec §2).
FOOD_INTENTS: frozenset[str] = frozenset({
    "definition",
    "food_standard",
    "requirement",
    "parameter_specific_standard",
    "compliance",
    "prohibition",
    "licensing",
    "sampling",
    "procedure",
    "penalty",
    "scope",
    "general_information",
})

#: Requested provision types (task spec §2/§3).
PROVISION_TYPES: frozenset[str] = frozenset({
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
})

#: Intent → default requested provision type (task spec examples).
_INTENT_TO_PROVISION: dict[str, str] = {
    "definition": "definition",
    "food_standard": "standard",
    "requirement": "requirement",
    "parameter_specific_standard": "standard",
    "compliance": "standard",
    "prohibition": "prohibition",
    "licensing": "licensing",
    "sampling": "sampling",
    "procedure": "procedure",
    "penalty": "penalty",
    "scope": "scope",
    "general_information": "general",
}

#: Well-known food-commodity synonyms → canonical name (only used to
#: *canonicalise an already-detected entity*, never to invent one).
COMMODITY_SYNONYMS: dict[str, str] = {
    "safed zeera": "cumin",
    "zeera": "cumin",
    "jeera": "cumin",
    "cuminum cyminum": "cumin",
    "kalonji": "cumin black",
    "nigella sativa": "cumin black",
    "dhania": "coriander",
    "coriandrum sativum": "coriander",
    "dalchini": "cinnamon",
    "elaichi": "cardamom",
    "haldi": "turmeric",
    "mirch": "chilli",
    "lal mirch": "chilli",
}


def commodity_synonyms() -> dict[str, str]:
    """Canonicalisation map (exposed for tests and the metadata adapter)."""
    return dict(COMMODITY_SYNONYMS)


#: Flattened commodity vocabulary (synonyms + canonical names) for
#: word-boundary entity detection — the metadata module's known-commodity
#: list mirrored here so query and chunk sides agree.
_COMMODITY_VOCAB: frozenset[str] = frozenset(
    {s.lower() for s in COMMODITY_SYNONYMS}
    | {c.lower() for c in COMMODITY_SYNONYMS.values()}
    | {
        "cumin",
        "coriander",
        "cinnamon",
        "cardamom",
        "turmeric",
        "chilli",
        "pepper",
        "clove",
        "cloves",
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
        "mustard",
        "curry powder",
        "asafoetida",
        "butter",
        "cheese",
        "ghee",
        "margarine",
        "honey",
        "jam",
        "jelly",
        "milk",
        "paneer",
        "flour",
        "maida",
        "besan",
        "rice",
        "wheat",
        "sugar",
        "jaggery",
        "salt",
        "vanaspati",
        "bread",
        "biscuit",
        "tea",
        "coffee",
        "cocoa",
        "meat",
        "fish",
        "egg",
        "poultry",
        "ice cream",
        "fruit squash",
        "confectionery",
        "bakery",
        "masala",
        "ajwan",
    },
)


def _load_harvested_commodities() -> frozenset[str]:
    """Corpus-harvested commodity names (query-side mirror of the chunk side).

    Reads the same ``commodity_vocabulary.json`` consumed by
    ``provision_metadata`` so query and chunk vocabularies can never drift.
    Only *names* are consumed — harvested synonym candidates are reviewed by
    hand before promotion (a wrong synonym hijacks every query containing
    the word).  Missing file -> empty set (base vocabulary unchanged).
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent / "commodity_vocabulary.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(n).lower() for n in (data.get("commodities") or []) if str(n).strip())
    except (OSError, ValueError, AttributeError):
        return frozenset()


#: Query-side vocabulary = base + harvested (union).  Longest-name-first
#: matching in :func:`extract_entity` means multi-word names ("dried mango
#: powder", "mixed masala") win over their head words.
_COMMODITY_VOCAB: frozenset[str] = frozenset({s.lower() for s in _COMMODITY_VOCAB} | _load_harvested_commodities())


# ---------------------------------------------------------------------------
# Intent patterns — ordered most-specific first
# ---------------------------------------------------------------------------

#: Parameter vocabulary (task spec §8/§14).
_PARAMETER_PATTERNS: dict[str, re.Pattern[str]] = {
    "moisture": re.compile(r"\bmoisture\b", re.IGNORECASE),
    "extraneous matter": re.compile(r"\bextraneous\s+matter\b|\bforeign\s+matter\b", re.IGNORECASE),
    "total ash": re.compile(r"\btotal\s+ash\b|\bash\s+content\b", re.IGNORECASE),
    "acid insoluble ash": re.compile(r"\bacid\s+insoluble\s+ash\b|\bash\s+insoluble\b", re.IGNORECASE),
    "volatile oil": re.compile(r"\bvolatile\s+oil\b", re.IGNORECASE),
    "insect damaged matter": re.compile(r"\binsect[\s-]*damaged?\b", re.IGNORECASE),
    "defective seeds": re.compile(r"\bdefective\s+seeds?\b|\bbroken\s+(?:fruits|seeds)\b", re.IGNORECASE),
    "lead": re.compile(r"\blead\b", re.IGNORECASE),
    "aflatoxin": re.compile(r"\baflatoxin\b", re.IGNORECASE),
    "pesticide residue": re.compile(r"\bpesticide\s+residue|\bmrl\b", re.IGNORECASE),
}

#: Intent patterns.  Each entry: (intent, [regexes]) — first intent whose
#: pattern count is maximal wins (mirrors classify_legal_query's count-wins).
_INTENT_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    # parameter_specific_standard — a named parameter + limit ask.  MUST beat
    # food_standard: "moisture limit for cumin" is about one parameter.
    (
        "parameter_specific_standard",
        [
            re.compile(
                r"\b(?:what(?:'s| is| are)|which)\s+(?:the\s+)?(?:maximum|minimum|permitted|allowed|prescribed)?\s*"
                r"(?:limit|level|content|value|requirement)s?\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"\b(?:moisture|extraneous\s+matter|foreign\s+matter|total\s+ash|acid\s+insoluble\s+ash|"
                r"volatile\s+oil|insect[\s-]*damaged|defective\s+seeds?|aflatoxin|lead)\b",
                re.IGNORECASE,
            ),
            re.compile(r"\bhow\s+much\s+(?:moisture|ash|extraneous|foreign)\b", re.IGNORECASE),
        ],
    ),
    # compliance — supplied facts judged against a standard
    (
        "compliance",
        [
            re.compile(r"\b(?:does|do|is|are)\b.{0,60}\bcomply\b|\bconform(?:s|ing)?\s+to\b", re.IGNORECASE),
            re.compile(r"\bcompliance\b.{0,40}\bstandard\b|\bmeets?\s+the\s+standard\b", re.IGNORECASE),
            re.compile(r"\bwithin\s+(?:the\s+)?(?:prescribed\s+)?limits?\b", re.IGNORECASE),
        ],
    ),
    # definition — "what is X" / "define X" with NO provision word
    (
        "definition",
        [
            re.compile(r"^\s*what\s+(?:is|are)\s+(?:a|an|the)?\s*[\"']?", re.IGNORECASE),
            re.compile(r"^\s*define\b", re.IGNORECASE),
            re.compile(r"\bdefinition\s+of\b|\bmeaning\s+of\b|\bwhat\s+is\s+meant\s+by\b", re.IGNORECASE),
        ],
    ),
    # food_standard — the standard itself
    (
        "food_standard",
        [
            re.compile(r"\bstandards?\b.{0,30}\bfor\b|\bfor\b.{0,30}\bstandards?\b", re.IGNORECASE),
            re.compile(r"\bstandard\s+(?:for|of|applicable)\b", re.IGNORECASE),
            re.compile(r"\bwhat\s+standards?\b", re.IGNORECASE),
            re.compile(r"\bshall\s+conform\b|\bconform\s+to\s+the\s+(?:following\s+)?standards?\b", re.IGNORECASE),
        ],
    ),
    # requirement — "what requirements apply"
    (
        "requirement",
        [
            re.compile(
                r"\brequirements?\b.{0,30}\b(?:for|of|apply|applicable)\b|\bwhat\s+requirements?\b", re.IGNORECASE,
            ),
            re.compile(r"\bspecifications?\s+(?:for|of)\b", re.IGNORECASE),
        ],
    ),
    # prohibition
    (
        "prohibition",
        [
            re.compile(
                r"\bprohibit(?:ed|ion)?\b|\b(?:banned?|ban\s+on)\b|\bshall\s+not\s+(?:be\s+)?(?:sold|used|added)\b",
                re.IGNORECASE,
            ),
        ],
    ),
    # licensing
    (
        "licensing",
        [
            re.compile(
                r"\blicen[cs]e\b|\blicensing\b|\bregistration\s+(?:requirement|for)\b|\bfbo\s+licen[cs]e\b",
                re.IGNORECASE,
            ),
        ],
    ),
    # sampling
    (
        "sampling",
        [
            re.compile(r"\bsampl(?:e|es|ing)\b.{0,40}\b(?:procedure|how|method|draw|taken)\b", re.IGNORECASE),
            re.compile(r"\bhow\s+(?:should|to|is)\b.{0,30}\bsampl\w*\b", re.IGNORECASE),
        ],
    ),
    # procedure
    (
        "procedure",
        [
            re.compile(r"\bprocedure\b|\bprocess\s+(?:for|of)\b|\bhow\s+(?:to|do|should)\b", re.IGNORECASE),
            # New: form/seizure/sampling related procedure questions
            re.compile(
                r"\b(seizure|sample|appeal|memorandum|form)\b.{0,80}\b(procedure|how|step|after|next)\b", re.IGNORECASE,
            ),
            re.compile(r"\bwhich form\b|\bwhat form\b|\bform\s+(?:ii|iii|iv|v|vi|vii|viii)\b", re.IGNORECASE),
        ],
    ),
    # penalty
    (
        "penalty",
        [
            re.compile(r"\bpenalt(?:y|ies)\b|\bpunish(?:ment|able)\b|\bfine\b|\bimprisonment\b", re.IGNORECASE),
        ],
    ),
    # scope
    (
        "scope",
        [
            re.compile(r"\bapplies\s+to\b|\bscope\b|\bapplicable\s+to\b|\bextend(?:s)?\s+to\b", re.IGNORECASE),
        ],
    ),
    # general_information — "unsafe food"/adulteration/nutrition knowledge
    # asks that are not provision asks.  Runs last (lowest priority).
    (
        "general_information",
        [
            re.compile(r"\bunsafe\s+food\b|\badulterat\w*\b|\bfood\s+safety\b|\bnutrition\b", re.IGNORECASE),
        ],
    ),
]

#: Words that indicate the query asks about a *provision* rather than the
#: substance itself — they flip a bare "what is X" to food_standard.
_PROVISION_WORD_RE = re.compile(
    r"\bstandards?\b|\brequirements?\b|\bspecifications?\b|\blimits?\b|\bparameters?\b",
    re.IGNORECASE,
)

_STRIP_WORDS_RE = re.compile(
    r"\b(?:what|which|who|whom|whose|is|are|was|were|the|a|an|of|for|to|in|on|under|as|per|"
    r"prescribed|specified|applicable|maximum|minimum|max|min|permitted|allowed|limit|level|"
    r"content|value|requirement|requirements|standard|standards|specification|specifications|"
    r"please|tell|me|about|give|show|does|do|did|comply|compliance|conform|conforms|"
    r"how|much|many|should|must|shall|and|with|by|at|be|being|been|it|its|"
    r"this|that|these|those|there|their|they|them|sample|sampled|samples|sampling|"
    r"procedure|process|licen[cs]e|licensing|registration|registered|penalt(?:y|ies)|"
    r"prohibit(?:ed|ion)?|banned?|restrict(?:ed|ion)?|sampl\w*|define|definition|"
    r"meaning|mean|means|meant|apply|applies|applying|need|needed|needs)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def extract_parameters(query: str) -> list[str]:
    """Ordered, de-duplicated parameter names mentioned in the query."""
    params: list[str] = []
    for name, pattern in _PARAMETER_PATTERNS.items():
        if pattern.search(query):
            params.append(name)
    return params


def extract_entity(query: str) -> str | None:
    """Extract the commodity/entity the query is about (or ``None``).

    Strategy: strip interrogative/provision/action boilerplate; the remaining
    tokens are the entity candidate.  A *known commodity word* wins outright
    (exact-vocabulary matching — never guesses); otherwise the residual
    phrase is returned for the reranker to score fuzzily.  A query of pure
    boilerplate ("What is the procedure?") yields ``None``.
    """
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return None
    q = q.strip("?.!,;:")
    low = q.lower()

    # 1) Known commodity vocabulary wins (word-boundary, longest first).
    for name in sorted(_COMMODITY_VOCAB, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            return COMMODITY_SYNONYMS.get(name, name)

    # 2) Residual after boilerplate strip — a fuzzy fallback for entities
    #    outside the vocabulary (documented, deterministic).
    residual = _STRIP_WORDS_RE.sub(" ", q)
    residual = re.sub(r"\s+", " ", residual).strip(" ,.:;?-")
    if not residual:
        return None
    words = [w for w in residual.split() if len(w) > 1 and not w.isdigit()]
    if not words:
        return None
    return " ".join(words).lower()


def _count_hits(patterns: list[re.Pattern[str]], query: str) -> int:
    return sum(1 for p in patterns if p.search(query))


def detect_food_intent(query: str) -> tuple[str, float]:
    """Classify the food-domain intent deterministically.

    Returns ``(intent, confidence)``.  Count-wins across pattern groups
    (most hits wins, ties resolved by list order — parameter first).
    """
    q = (query or "").strip()
    if not q:
        return "general_information", 0.0

    scores: dict[str, int] = {}
    for intent, patterns in _INTENT_PATTERNS:
        hits = _count_hits(patterns, q)
        if hits > 0:
            scores[intent] = hits
    if not scores:
        return "general_information", 0.0

    # A leading "what is the <provision-word> …" shape is NOT a definition
    # ask — the definition pattern ("^\s*what is") over-fires on it.  When a
    # specific provision intent (sampling/penalty/licensing/prohibition/
    # procedure) matched, it wins outright over the incidental definition
    # hit; the bare-definition flip below only handles "what is the standard
    # for X" (a *request* for the provision, not a named provision word).
    _specific_provisions = ("sampling", "penalty", "licensing", "prohibition", "procedure")
    specific = {k: v for k, v in scores.items() if k in _specific_provisions}
    if specific and scores.get("definition"):
        best_specific = max(specific.items(), key=lambda kv: kv[1])[0]
        return best_specific, min(0.6 + 0.2 * specific[best_specific], 1.0)

    best = max(scores.items(), key=lambda kv: kv[1])[0]

    # "What is the standard for cumin?" — the definition pattern
    # ("^\s*what is") fires, but the presence of a provision word flips the
    # bare definition ask to the provision it names.
    if best == "definition" and _PROVISION_WORD_RE.search(q):
        if re.search(r"\bstandards?\b", q, re.IGNORECASE):
            return "food_standard", 0.9
        return "requirement", 0.8

    # "What is the penalty for unsafe food?" — the definition pattern fires
    # ("what is\b" inside "what is the penalty"), but a *higher-priority*
    # intent also matched.  When the definition ask is not a bare "what is
    # X" shape (i.e. more words follow "what is ..." than the entity), the
    # next-best non-definition intent wins.
    if best == "definition":
        others = {k: v for k, v in scores.items() if k != "definition"}
        if others:
            runner_up, runner_hits = max(others.items(), key=lambda kv: kv[1])
            # A bare "what is X" / "define X" has exactly one definition hit
            # and no competing signal — keep definition.  Otherwise the
            # competing intent (penalty/licensing/…) is the real ask.
            bare_definition = bool(re.match(r"^\s*(?:what\s+(?:is|are)|define)\s+[^?]{1,60}\??\s*$", q, re.IGNORECASE))
            if not bare_definition and runner_hits > 0:
                return runner_up, min(0.6 + 0.2 * runner_hits, 1.0)

    hits = scores[best]
    confidence = min(0.6 + 0.2 * hits, 1.0)
    return best, confidence


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoodQueryUnderstanding:
    """The food-commodity view of one query (task spec §2 schema)."""

    query: str
    entity: str | None
    intent: str
    target: str
    parameters: list[str] = field(default_factory=list)
    jurisdiction: str | None = None
    requested_provision_type: str = "general"
    confidence: float = 0.0

    @property
    def wants_standard(self) -> bool:
        """Whether the query requests a standard/limit provision (not a definition)."""
        return self.intent in {
            "food_standard",
            "requirement",
            "parameter_specific_standard",
            "compliance",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity or "unknown",
            "intent": self.intent,
            "target": self.target,
            "parameters": list(self.parameters),
            "jurisdiction": self.jurisdiction,
            "requested_provision_type": self.requested_provision_type,
            "confidence": round(self.confidence, 4),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_query(cls, query: str, jurisdiction: str | None = None) -> FoodQueryUnderstanding:
        """Parse ``query`` into the food view (deterministic)."""
        intent, confidence = detect_food_intent(query)
        entity = extract_entity(query)
        parameters = extract_parameters(query)

        # Parameter-specific intent requires at least one known parameter;
        # otherwise it degrades to food_standard (the limit ask is generic).
        if intent == "parameter_specific_standard" and not parameters:
            intent = "food_standard"

        # Target = the provision/information the query points at.
        target = "definition" if intent == "definition" else _INTENT_TO_PROVISION.get(intent, "general")
        if intent == "parameter_specific_standard" and parameters:
            target = f"{parameters[0]} limit"

        return cls(
            query=query or "",
            entity=entity,
            intent=intent,
            target=target,
            parameters=parameters,
            jurisdiction=jurisdiction,
            requested_provision_type=_INTENT_TO_PROVISION.get(intent, "general"),
            confidence=confidence,
        )
