"""Harvest the commodity vocabulary from the corpus itself (one-shot, deterministic).

Reads the cached payload index (``evaluation/out/cache/payload_index.jsonl``,
all collections), extracts every clause-lead heading of the form::

    2.9.8: CUMIN (ZEERA, KALONJI) 1. Cumin whole means ...
    2.3.22 Ginger Cocktail: 1. Ginger Cocktail (Ginger Beer Or Gingerale) means ...

and emits ``app/rag/retrieval/commodity_vocabulary.json``::

    {
      "generated_at": ...,
      "source": ...,
      "commodities": [ ... primary names, sorted ... ],
      "synonyms": { "synonym": "canonical", ... },
      "compounds": [ ... multi-word product names ("ginger cocktail") ... ],
      "stats": { ... per-family counts, skipped headings ... }
    }

Design constraints (mirroring the retrieval code):
* **Deterministic** — same index in, same vocabulary out; no LLM.
* **Never invent** — a name is a heading's leading noun phrase, validated
  against shape rules; anything ambiguous is recorded under ``stats.skipped``.
* **Phrase-aware** — compound products ("Ginger Cocktail", "Milk Powder")
  are harvested whole; the bare commodity word is harvested separately when
  it leads its own clause ("Cumin").

Usage:
    python -m evaluation.harvest_commodity_vocabulary [--index PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

INDEX_PATH = PROJECT_ROOT / "evaluation" / "out" / "cache" / "payload_index.jsonl"
OUT_PATH = PROJECT_ROOT / "app" / "rag" / "retrieval" / "commodity_vocabulary.json"

#: Clause-lead heading: "2.9.8: CUMIN (ZEERA, KALONJI) ..." — number, colon,
#: then the name (up to the sub-item marker "1." or sentence end).
_CLAUSE_LEAD_RE = re.compile(r"^\s*(?P<clause>\d+(?:\.\d+)+)[:.]?\s+(?P<name>[A-Z][A-Za-z ()&,'-]*?)(?=\s+\d+\.|\s*\(|$|:)", re.MULTILINE)

#: Also accept "2.9.8 CUMIN ..." without colon.
_CLAUSE_LEAD_NO_COLON_RE = re.compile(r"^\s*(?P<clause>\d+(?:\.\d+)+)\s+(?P<name>[A-Z][A-Za-z ()&,'-]{2,}?)(?=\s+\d+\.|\s*\(|$|:)", re.MULTILINE)

#: Sub-definition lead inside a clause: "3. Cardamom (Chhoti Elaichi) powder means ..."
_SUBDEF_RE = re.compile(r"\b\d+\.\s+(?P<name>[A-Z][A-Za-z ()&,'-]{2,}?)\s+means\b")

#: Boilerplate words that disqualify a candidate from being a commodity name.
_STOP_NAME_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "for", "in", "on", "or", "and", "to", "by",
        "use", "used", "per", "cent", "percent", "maximum", "minimum", "limit",
        "shall", "may", "means", "standard", "standards", "product", "products",
        "packaging", "labelling", "regulation", "regulations", "appendix",
        "part", "sl", "name", "article", "food", "chapter", "column",
    },
)

#: Words that, when the *whole* candidate is just them, are furniture.
_BARE_FURNITURE: frozenset[str] = frozenset(
    {
        "artificial", "artificially", "sweetened", "synthetic", "synthetically",
        "name", "sl", "no", "details", "particulars", "general", "provision",
        "carriage", "freight", "cases", "packing", "package", "packages",
        "label", "labelled", "declaration", "restrictions", "restriction",
        "hygiene", "contaminants", "toxins", "residues", "other", "miscellaneous",
    },
)

#: Administrative/provision words — a candidate containing ANY of these is a
#: section heading or requirement, never a commodity ("Appeal", "Definition",
#: "Cleaning and sanitation", "Declaration of alcohol content", ...).
_ADMIN_WORDS: frozenset[str] = frozenset(
    {
        "appeal", "appeals", "definition", "definitions", "declaration",
        "declarations", "brand", "owner", "cleaning", "sanitation",
        "maintenance", "interior", "structure", "structures", "drainage",
        "waste", "disposal", "records", "record", "weighing", "scales",
        "licence", "license", "licensing", "penalty", "penalties",
        "procedure", "procedures", "storage", "transport", "vehicles",
        "cargo", "compartments", "containers", "chemicals", "hazardous",
        "imported", "irradiation", "manner", "conditions", "forfeiture",
        "offence", "offences", "analysis", "enforcement", "separately",
        "semi", "particulars", " particulars", "provisions", "prohibition",
        "holding", "health", "status", "illness", "injuries", "audit",
        "inspection", "receipt", "portioning", "display", "service",
        "preparation", "transportation", "requirements", "exemptions",
        "handling", "areas", "primary", "operations", "facilities",
        "personal", "behaviour", "behavior", "cleanliness", "visitors",
        "training", "washing", "hands", "supply", "control", "pest",
        "temperature", "payment", "mode", "scheme", "facilitators",
        "registration", "petty", "commencement", "title", "testing",
        "reheating", "loading", "slaughter", "producer", "importer",
        "return", "procurement", "premises", "rooms", "packaging",
        "labelling", "labeling", "standards", "wrapping", "effect",
        "effects", "clothing", "support", "supports", "altogether",
    },
)

#: A candidate STARTING with one of these is a sentence fragment, not a name.
_FIRST_WORD_REJECT: frozenset[str] = frozenset(
    {
        "in", "for", "the", "of", "at", "on", "with", "as", "these",
        "this", "all", "other", "such", "any", "and", "or", "to", "by",
        "from", "their", "where", "when", "use", "used", "per",
    },
)


def _clean_name(raw: str) -> str:
    """Normalise a harvested name: strip furniture, collapse dup words/spaces."""
    name = raw.strip(" :.,;()-&")
    name = re.sub(r"\s+", " ", name)
    # Collapse consecutive duplicate words ("Asafoetida Asafoetida") and
    # "X and X" patterns ("safflowerseed oil and safflowerseed oil").
    name = re.sub(r"\b(\w+)( \1\b)+", r"\1", name, flags=re.IGNORECASE)
    name = re.sub(r"^(.+?) and \1$", r"\1", name, flags=re.IGNORECASE)
    return name.strip()


def _valid_name(name: str) -> bool:
    """Shape validation: 1-5 alphabetic words, no boilerplate/admin names."""
    if not (2 <= len(name) <= 44):
        return False
    words = name.lower().split()
    if not (1 <= len(words) <= 5):
        return False
    if words[0] in _FIRST_WORD_REJECT:
        return False
    if all(w in _BARE_FURNITURE or w in _STOP_NAME_WORDS for w in words):
        return False
    if any(w in _ADMIN_WORDS for w in words):
        return False
    if not all(re.match(r"^[a-z][a-z-]*$", w) for w in words):
        return False
    return True


def harvest(index_path: Path = INDEX_PATH) -> dict:
    """Extract commodity names from clause-lead headings across all collections."""
    from app.rag.retrieval.provision_metadata import (
        _COMMODITY_MODIFIERS,
        _FORM_WORDS,
        _PRODUCT_WORDS,
    )

    #: (canonical_name_lower -> {"clause": ..., "doc": ..., "evidence": ...})
    names: dict[str, dict] = {}
    #: (synonym_lower -> canonical_lower) — parentheticals after the lead name.
    synonyms: dict[str, dict] = {}
    compounds: set[str] = set()
    family_counts: Counter = Counter()
    skipped: list[dict] = []
    seen_clauses: set[tuple[str, str]] = set()

    def record(name: str, clause: str, doc: str, evidence: str) -> None:
        key = name.lower()
        if key not in names:
            names[key] = {"clause": clause, "document_id": doc, "evidence": evidence[:120]}

    def record_synonym(syn: str, canonical: str, clause: str) -> None:
        s = syn.lower().strip()
        c = canonical.lower().strip()
        if s and c and s != c and s not in names:
            synonyms.setdefault(s, {"canonical": c, "clause": clause})

    with open(index_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            pl = rec.get("payload") or {}
            text = str(pl.get("chunk_text") or "")
            clause = str(pl.get("clause_number") or "")
            doc = str(pl.get("document_id") or "")
            if not text:
                continue

            # Only clause-lead chunks declare commodity identity; row fragments
            # and prose never invent names.
            m = _CLAUSE_LEAD_RE.match(text) or _CLAUSE_LEAD_NO_COLON_RE.match(text)
            if not m:
                continue
            lead_clause = m.group("clause")
            key = (doc, lead_clause)
            if key in seen_clauses:
                continue
            seen_clauses.add(key)

            name = _clean_name(m.group("name"))
            fam = ".".join(lead_clause.split(".")[:2])
            if not _valid_name(name):
                skipped.append({"clause": lead_clause, "candidate": name[:60]})
                continue
            # Two-tier confidence: a real commodity clause *defines* its
            # subject ("... means ..."); operational headings never do.
            # Title-only names (heading chunk without the definition text)
            # survive only when short (<=3 words) — catch-all for
            # heading-only clauses like 2.9.20 MIXED MASALA.
            if "means" not in text.lower() and len(name.split()) > 3:
                skipped.append({"clause": lead_clause, "candidate": name[:60], "reason": "no-means-long"})
                continue
            # Joint names ("Dahi or Curd", "Tomato Ketchup and Tomato Sauce")
            # cover several commodities — record each side separately when
            # both sides are valid names, else keep the joint name.
            parts = re.split(r"\s+\b(?:or|and)\s+", name)
            recorded = False
            if len(parts) == 2:
                a, b = _clean_name(parts[0]), _clean_name(parts[1])
                if _valid_name(a) and _valid_name(b) and 1 <= len(a.split()) <= 3 and 1 <= len(b.split()) <= 3:
                    record(a, lead_clause, doc, text[:120])
                    record(b, lead_clause, doc, text[:120])
                    family_counts[fam] += 1
                    recorded = True
            if not recorded:
                record(name, lead_clause, doc, text[:120])
                family_counts[fam] += 1

            # Parenthetical synonyms: "CUMIN (ZEERA, KALONJI)".
            pm = re.search(r"\(([^)]{2,60})\)", text[len(m.group("clause")) : len(m.group("clause")) + len(name) + 70])
            if pm:
                for part in re.split(r"[,;]", pm.group(1)):
                    syn = _clean_name(part)
                    if not _valid_name(syn) or syn.lower() == name.lower():
                        continue
                    words_l = syn.lower().split()
                    if any(w in _ADMIN_WORDS or w in _STOP_NAME_WORDS for w in words_l):
                        continue
                    if "known as" in syn.lower() or "commonly" in syn.lower():
                        continue
                    record_synonym(syn, name, lead_clause)

            # Compound continuation: lead name followed by a product/modifier/
            # known word ("Ginger Cocktail", "Milk Powder") — harvest the
            # two-word compound as its own commodity when the next word is
            # part of the name phrase, else keep the single word.
            tail = text[m.end() :]
            nxt = re.match(r"\s*([A-Za-z]+)", tail)
            if nxt:
                w = nxt.group(1).lower()
                if w in _PRODUCT_WORDS or w in _COMMODITY_MODIFIERS:
                    record(f"{name} {w}".strip(), lead_clause, doc, text[:120])
                    compounds.add(f"{name.lower()} {w}")

    # Sub-definitions ("3. Cardamom (Chhoti Elaichi) powder means ...") add
    # form variants only for already-known lead names (no new identities).
    with open(index_path, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            text = str((rec.get("payload") or {}).get("chunk_text") or "")
            if "means" not in text:
                continue
            for sd in _SUBDEF_RE.finditer(text):
                cand = _clean_name(sd.group("name"))
                words = cand.lower().split()
                # Keep only when the head word is already a harvested name or
                # the phrase strips a form word to one.
                head = words[0] if words else ""
                if head in names:
                    form = words[-1]
                    if form in _FORM_WORDS and len(words) == 2:
                        record_synonym(f"{head} {form}", head, "")
    return {
        "commodities": sorted(names),
        # NOT wired into the runtime vocab automatically: a wrong synonym
        # (e.g. "edible" -> "catechu") would hijack every query containing
        # the word.  Reviewed/promoted candidates only.
        "candidate_synonyms": {k: v["canonical"] for k, v in sorted(synonyms.items())},
        "compounds": sorted(compounds),
        "stats": {
            "families": dict(sorted(family_counts.items())),
            "skipped": skipped,
            "n_commodities": len(names),
            "n_synonyms": len(synonyms),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=str(INDEX_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    vocab = harvest(Path(args.index))
    print(f"commodities: {vocab['stats']['n_commodities']}")
    print(f"synonyms:    {vocab['stats']['n_synonyms']}")
    print(f"families:    {vocab['stats']['families']}")
    print(f"skipped:     {len(vocab['stats']['skipped'])}")
    for s in vocab["stats"]["skipped"][:15]:
        print("  skip:", s)
    print()
    print("commodity list:")
    for c in vocab["commodities"]:
        print("  ", c)
    if vocab["candidate_synonyms"]:
        print("synonym sample:", dict(list(vocab["candidate_synonyms"].items())[:15]))
    if not args.dry_run:
        OUT_PATH.write_text(json.dumps(vocab, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
