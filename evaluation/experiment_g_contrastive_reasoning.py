"""Experiment G: contrastive statutory reasoning over the frozen generation cohort.

Milestone 2 of ``docs/plans/CONTRASTIVE_STATUTORY_REASONING_PLAN.md``.

Three stages, replacing freeform answer generation and open self-critics:

**Stage 1 -- candidate span resolution (deterministic, 0 LLM).**
The frozen prompt window is grouped into candidate statutory spans (one per
provision where the metadata supports it, one per chunk otherwise).  For each
span: the operative rule (the sentence with the highest overlap with the
question's own words), any proviso or exception, any penalty ladder, and the
enforcing authority.  Spans are ranked by question relevance and the top N
are offered to the adjudicator.

**Stage 2 -- contrastive adjudication (one constrained call).**
The question's facts plus the N candidate spans go to a single structured
call that must pick the governing span -- or, when the answer genuinely
spans two provisions, the two provisions.  The output format is fixed and
machine-checkable, so validation needs no second call and no open-ended
critic -- the failure mode that made Experiment D3 regress.

**Stage 3 -- gated verification and fallback (deterministic, 0 LLM).**
The adjudication is accepted only if the span id was offered, the rule is
verbatim from it, the citations are real window indices, the answer cites
the selected span, and the answer does not report the rule as absent.
Otherwise the **cached baseline answer is emitted** -- the plan's "reject
and keep baseline" rule, and it costs nothing because the baseline is
frozen in the cohort.

Two mechanical slips are repaired rather than rejected, because they are
formatting and not fabrication: the adjudicator writes candidate ids where
source numbers belong (``[S28]`` for ``[3]``) and, less often, source
numbers where candidate ids belong.  Both are resolved through the prompt's
own mapping.  A reference that names no offered span is left alone, so the
gate still rejects anything it cannot resolve.

The gate guarantees *no fabrication*, not *no regression*: a verified
adjudication can still reach the wrong legal conclusion.  A regression check
against the baseline is therefore reported as a MEASUREMENT, and
``--fallback-on-regression`` turns it into the selection rule for the
experiment.  The headline number is always reported for both rules.

Model: ``nvidia/nemotron-3.5-lightning:free`` -- the whitelisted
experimental answer model.  It is pinned here and asserted at call time; no
other model is used.

Usage:
    python -m evaluation.experiment_g_contrastive_reasoning --dry-run
    python -m evaluation.experiment_g_contrastive_reasoning --limit 10
    python -m evaluation.experiment_g_contrastive_reasoning
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

#: The whitelisted experimental answer model.  Pinned, not configurable: the
#: cohort was frozen against this model and the comparison is only valid
#: against it.
MODEL = "nvidia/nemotron-3.5-lightning:free"

from evaluation.answer_scoring import score_answer  # noqa: E402
from evaluation.benchmark import load_questions  # noqa: E402

CEIL_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
COHORT_FILE = CEIL_DIR / "generation_cohort.json"
OUT_FILE = CEIL_DIR / "contrastive_reasoning.json"

#: How many candidate spans the adjudicator is offered.
#:
#: Stage 1's recall against the governing (primary + acceptable) gold, over
#: the 59-question cohort:
#:
#:     max_spans   6     10     15     20     30     40
#:     recall   73%    81%    86%    90%    95%    95%
#:
#: 20 is the default: 90% of the cohort is reachable and the median prompt is
#: ~2.3k tokens.  Recall saturates at 30, so the extra candidates cost prompt
#: without buying coverage.  The figures come from
#: ``--dry-run``, which reports the same measurement.
DEFAULT_MAX_SPANS = 20

#: Per-question soft delta below which the adjudicated answer is considered
#: equivalent to the baseline (the measured same-config soft sd is 0.046).
#: Used only by the optional regression fallback.
REGRESSION_SOFT_TOLERANCE = 0.05

# ---------------------------------------------------------------------------
# Stage 1 -- candidate span resolution
# ---------------------------------------------------------------------------

#: Splits on sentence-final punctuation followed by a capital, digit or
#: bracket.  Statutory text is OCR-noisy, so the class also accepts the
#: subsection markers "(1)" "(2)" that begin a provision.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

#: Question-side stopwords.  Deliberately does NOT include the statutory
#: modals: "may" and "shall" carry meaning when matching a span, and the
#: question's own modals are usually noise.
_STOP = frozenset([
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "to",
    "in",
    "on",
    "for",
    "under",
    "per",
    "with",
    "by",
    "is",
    "are",
    "be",
    "as",
    "that",
    "this",
    "which",
    "what",
    "where",
    "when",
    "who",
    "how",
    "it",
    "its",
    "from",
    "at",
    "such",
    "any",
    "all",
    "not",
    "no",
    "if",
    "than",
    "then",
    "there",
    "their",
    "they",
    "them",
    "he",
    "she",
    "his",
    "her",
    "has",
    "have",
    "had",
    "been",
    "being",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "will",
    "shall",
    "may",
    "must",
    "also",
    "into",
    "about",
    "over",
    "between",
])

#: Statutory modal verbs that mark an operative rule.
_MODALS = frozenset([
    "shall",
    "may",
    "must",
    "is",
    "are",
    "empowered",
    "liable",
    "required",
    "permitted",
    "prohibited",
    "bound",
    "entitled",
    "obliged",
    "authorized",
    "authorised",
    "has",
    "have",
])

#: Proviso and exception openers.
_PROVISO_RE = re.compile(
    r"^\s*(provided|provided that|except|except that|unless|subject to|notwithstanding|"
    r"but|however|save|save as|in the case of|where|whenever)\b",
    re.IGNORECASE,
)

#: Continuing-fine / escalation markers -- the multi-tier penalty structure.
_PENALTY_LADDER_RE = re.compile(
    r"\b(continuing|further sum|further fine|per day|each day|for every day|per month|"
    r"every subsequent|second or subsequent|repeat|subsequent conviction|"
    r"escalat|shall be liable to pay|twice|three times|additional)\b",
    re.IGNORECASE,
)

#: Roles that hold a statutory power.
_AUTHORITY_RE = re.compile(
    r"\b([A-Z][A-Za-z]*(?:\s+(?:of\s+)?[A-Z][A-Za-z]*){0,3}\s+"
    r"(?:Officer|Officers|Authority|Commissioner|Analyst|Magistrate|Government|Board))\b"
)

#: Gazette / notification noise -- never the governing provision.
_NOISE_TITLE_RE = re.compile(r"(gazette|notification|^\s*form\b|schedule)", re.IGNORECASE)

#: Words that carry no Act-identity: two "Act" names that differ only by these
#: are the same Act.
_ACT_NOISE = frozenset([
    "act",
    "acts",
    "rules",
    "rule",
    "regulation",
    "regulations",
    "order",
    "ordinance",
    "the",
    "of",
    "and",
    "amendment",
    "amended",
    "under",
])


def _act_key(name: str) -> set[str]:
    return {t for t in _tokens(name) if t not in _ACT_NOISE and not t.isdigit()}


def _act_matches(span_act: str, named_act: str) -> bool:
    """True when two Act names denote the same instrument."""
    a, b = _act_key(span_act), _act_key(named_act)
    if not a or not b:
        return False
    return len(a & b) >= max(1, min(len(a), len(b)) // 2)


#: Spelled-out numbers.  Statutory figures are routinely written out --
#: "a fine which may extend to five lakh rupees", "imprisonment for a term
#: which may extend to six months" -- so a figure detector that only looks
#: at digits misses most of them.
_NUMBER_WORDS = frozenset([
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "thousand",
    "million",
    "billion",
    "lakh",
    "crore",
])


def _has_figure(sentence: str) -> bool:
    """A statutory figure (amount, term, threshold) -- a year does not count."""
    for token in re.findall(r"\d[\d,.]*", sentence):
        digits = token.replace(",", "").replace(".", "")
        if len(digits) == 4 and digits.isdigit() and 1800 <= int(digits) <= 2100:
            continue
        return True
    words = set(re.findall(r"[a-z]+", sentence.lower()))
    return bool(words & _NUMBER_WORDS)


def _is_heading(sentence: str, has_modal: bool, has_figure: bool) -> bool:
    """A title-like line is a section heading, not an operative rule.

    "Punishment for obstructing or impersonating a Food Safety Officer." and
    "Penalty for misleading advertisement." name the provision; they do not
    state it.  A heading wins the overlap test easily -- it is made of the
    question's own words -- so it has to be demoted explicitly, or the
    adjudicator is handed a title instead of a rule.
    """
    if has_modal or has_figure:
        return False
    return len(sentence) <= 120


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content_words(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in _STOP and len(t) > 2}


def split_sentences(text: str) -> list[str]:
    """Split statutory text into sentences, tolerating OCR noise."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for part in _SENTENCE_RE.split(line):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _normalise(text: str) -> str:
    """Whitespace/case-insensitive form used for the verbatim check."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


@dataclass
class Span:
    """One candidate statutory provision extracted from the frozen window."""

    span_id: str
    act: str
    section: str | None
    document_type: str
    document_title: str
    text: str
    chunk_ids: list[str]
    citation_indices: list[int]
    operative_rule: str = ""
    operative_excerpt: str = ""
    operative_is_heading: bool = False
    proviso_or_exception: str = ""
    penalty_ladder: str = ""
    enforcement_authority: str = ""
    score: float = 0.0
    rank: int = 0
    group_total: int = 0
    score_parts: dict[str, float] = field(default_factory=dict)

    @property
    def authority_id(self) -> str:
        if self.section:
            return f"{self.act}, Section {self.section}"
        return f"{self.act} (no section identified)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "authority_id": self.authority_id,
            "act": self.act,
            "section": self.section,
            "document_type": self.document_type,
            "document_title": self.document_title,
            "chunk_ids": self.chunk_ids,
            "citation_indices": self.citation_indices,
            "operative_rule": self.operative_rule,
            "operative_excerpt": self.operative_excerpt,
            "proviso_or_exception": self.proviso_or_exception,
            "penalty_ladder": self.penalty_ladder,
            "enforcement_authority": self.enforcement_authority,
            "score": round(self.score, 4),
            "rank": self.rank,
            "score_parts": {k: round(v, 4) for k, v in self.score_parts.items()},
        }


def _group_key(chunk: dict[str, Any]) -> tuple[str, str]:
    """Chunks of the same provision group together; unlabelled chunks stand alone."""
    act = str(chunk.get("act_name") or chunk.get("document_title") or "")
    section = chunk.get("section_number")
    if section:
        return (act, f"sec:{section}")
    return (act, f"chunk:{chunk.get('chunk_id')}")


def extract_spans(
    question_text: str,
    window_chunks: list[dict[str, Any]],
    citation_index: dict[str, int],
    *,
    max_spans: int = DEFAULT_MAX_SPANS,
) -> list[Span]:
    """Stage 1: deterministic candidate span extraction and ranking.

    Returns the top ``max_spans`` spans, each with a ``group_total`` field
    recording how many groups the window held in total -- the recall figure
    the dry-run reports.
    """
    from app.rag.retrieval.identifier import detect_act, detect_sections

    question_words = _content_words(question_text)
    named_sections = {str(s) for s in (detect_sections(question_text) or [])}
    named_act = detect_act(question_text)
    penalty_question = bool(
        re.search(r"\b(penalt|fine|imprison|liable|prohibit|summons)", question_text, re.IGNORECASE)
    )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in window_chunks:
        groups.setdefault(_group_key(chunk), []).append(chunk)

    spans: list[Span] = []
    for n, (key, members) in enumerate(sorted(groups.items()), start=1):
        members.sort(key=lambda c: citation_index.get(str(c.get("chunk_id")), 9999))
        text = "\n".join(str(c.get("text") or "") for c in members)
        section = next((c.get("section_number") for c in members if c.get("section_number")), None)
        indices = [citation_index[str(c.get("chunk_id"))] for c in members if str(c.get("chunk_id")) in citation_index]

        sentences = split_sentences(text)

        # The operative rule: the sentence carrying the most of the question's
        # own words, preferring one that states a power or duty and demoting
        # title-like lines, which win the overlap test for the wrong reason.
        best_sentence, best_score = "", -1.0
        for sentence in sentences:
            words = _content_words(sentence)
            if not words:
                continue
            overlap = len(words & question_words) / (len(words) ** 0.5)
            has_modal = any(m in _MODALS for m in re.findall(r"[a-z]+", sentence.lower()))
            has_figure = _has_figure(sentence)
            if has_modal:
                overlap *= 1.15
            if penalty_question and has_figure:
                overlap *= 1.20
            if _is_heading(sentence, has_modal, has_figure):
                overlap *= 0.30
            if overlap > best_score:
                best_sentence, best_score = sentence, overlap
        operative = best_sentence or (sentences[0] if sentences else "")

        # The EXCERPT: the operative sentence plus the neighbouring sentences
        # that carry a figure.  A penalty ladder lives in separate sentences
        # from the one that names the offence ("...shall be punished with
        # imprisonment for a term which may extend to six months and with a
        # fine which may extend to five lakh rupees"), and the adjudicator
        # cannot copy figures it was never shown.  Showing one sentence made
        # it answer "a fine not exceeding ten thousand rupees" where the
        # reference says "imprisonment up to six months and a fine up to five
        # lakh rupees".  Every figure-bearing sentence in the span is
        # therefore included, in document order.
        excerpt_sentences: list[str] = []
        if operative and operative in sentences:
            start = sentences.index(operative)
            excerpt_sentences.append(operative)
            for candidate in sentences[start + 1 :]:
                if _has_figure(candidate):
                    excerpt_sentences.append(candidate)
                if len(excerpt_sentences) >= 4:
                    break
        excerpt = " ".join(excerpt_sentences) or operative

        proviso = " ".join(s for s in sentences if _PROVISO_RE.match(s))[:600]
        ladder = " ".join(s for s in sentences if _PENALTY_LADDER_RE.search(s))[:600]
        authorities = []
        for sentence in sentences:
            for match in _AUTHORITY_RE.findall(sentence):
                if match not in authorities:
                    authorities.append(match)
        authority = "; ".join(authorities[:3])

        span = Span(
            span_id=f"S{n}",
            act=str(members[0].get("act_name") or members[0].get("document_title") or ""),
            section=str(section) if section else None,
            document_type=str(members[0].get("document_type") or ""),
            document_title=str(members[0].get("document_title") or ""),
            text=text,
            chunk_ids=[str(c.get("chunk_id")) for c in members],
            citation_indices=indices,
            operative_rule=operative.strip(),
            operative_excerpt=excerpt.strip(),
            operative_is_heading=bool(operative)
            and _is_heading(
                operative,
                any(m in _MODALS for m in re.findall(r"[a-z]+", operative.lower())),
                _has_figure(operative),
            ),
            proviso_or_exception=proviso,
            penalty_ladder=ladder,
            enforcement_authority=authority,
        )

        # Ranking.  Uses ONLY the question and the context -- never the
        # benchmark's gold concepts or reference conclusion, which are the
        # answer key.
        #
        # The weights are deliberately modest on metadata: the section label
        # is a WEAK signal (an amending chunk inherits the section it
        # amends, and a chunk inserted into s69 can be labelled with the
        # amended s498), while the operative sentence's match to the
        # question's own words is the strong one.  Stage 1's contract is
        # RECALL -- the adjudicator does the discriminating -- so the ranking
        # must not crowd out a candidate that plainly states the answer.
        parts: dict[str, float] = {}
        parts["rule_match"] = round(best_score, 2) * 4.0
        if span.section and span.section in named_sections:
            # A section number alone is not identity: the Delhi Police Act and
            # the FSS Act both have a section 59.
            if not named_act or _act_matches(span.act, named_act):
                parts["named_section"] = 8.0
            else:
                parts["named_section_other_act"] = -10.0
        if span.section:
            parts["has_section"] = 2.0
        if authority:
            parts["has_authority"] = 2.0
        if proviso:
            parts["has_proviso"] = 2.5
        if ladder:
            parts["has_penalty_ladder"] = 2.5
        if _NOISE_TITLE_RE.search(span.document_title or ""):
            parts["noise_title"] = -6.0
        if re.search(r"amendment", span.document_title or "", re.IGNORECASE):
            # An amending Act describes a change; it is sometimes the only
            # carrier of a newly inserted provision, so demote, do not exclude.
            parts["amendment_document"] = -2.0
        if span.document_type == "regulation" and not span.section:
            parts["unlabelled_regulation"] = -1.0
        span.score_parts = parts
        span.score = sum(parts.values())
        spans.append(span)

    spans.sort(key=lambda s: (-s.score, s.span_id))
    for i, span in enumerate(spans, start=1):
        span.rank = i
    total = len(spans)
    kept = spans[:max_spans]
    for span in kept:
        span.group_total = total
    return kept


def select_deterministically(spans: list[Span], keep: int = 3) -> list[Span]:
    """The variance fix: select the governing spans WITHOUT the model.

    The measured failure of the model-selected arm is not signal but
    variance: two runs of the same arm picked different provisions (mean
    abs soft delta 0.0463, against an answer-level noise floor of 0.034).
    Most of the adjudication is a *selection* over candidates whose texts
    are already in the prompt, and Stage 1 already scores every candidate
    with deterministic signals (operative-sentence overlap, section match,
    authority match).  So the selection is taken from that ranking and the
    model is left only the composition of the answer.

    The top of the Stage 1 ranking is used as-is: the ranking's contract is
    recall (90% at 20 spans), and this variant needs only the top few.
    """  # noqa: D205
    return list(spans[:keep])


#: A clause is appended for a selected span only when the baseline answer
#: does not already carry it: at least this fraction of the span's operative
#: content-words must be MISSING from the baseline.
PATCH_COVER_THRESHOLD = 0.60

#: Appended clauses per answer.  Each clause is verbatim statute, and the
#: soft scorer penalises non-gold tokens, so the cap bounds the damage a
#: mis-ranked span can do.
PATCH_MAX_CLAUSES = 6

#: The v2 length gate is length delta <= +10%, so the patch is BUDGETED: the
#: appended clauses may add at most this share of the baseline's words (with
#: a floor so very short answers can still get one clause).  Measured on the
#: cohort: without the budget the best coverage config costs +53% median
#: length; with it, coverage +0.0063 / soft +0.0015 at +7.8% mean length.
PATCH_BUDGET_PCT = 10
PATCH_BUDGET_FLOOR = 10

#: The clause framing ("Under Section 38 of ... Act: "..." [3]") costs about
#: this many words over the excerpt itself.
CLAUSE_FRAME_WORDS = 8

#: The patch pool: spans ranked 1..keep by Stage 1.  Top-3 was too narrow
#: (coverage +0.019); the sweep found the top 8 add the clauses that carry
#: the missing figures.
PATCH_SPAN_POOL = 8


def compose_patch(
    baseline_answer: str,
    selected_spans: list[Span],
    *,
    question_text: str = "",
    max_clauses: int = PATCH_MAX_CLAUSES,
    cover_threshold: float = PATCH_COVER_THRESHOLD,
    budget_pct: int = PATCH_BUDGET_PCT,
    budget_floor: int = PATCH_BUDGET_FLOOR,
) -> tuple[str, list[Span]]:
    """Deterministic composition: the baseline answer plus verbatim clauses.

    The measured end-state of the model arms: selection can be made
    deterministic, but composition -- the LLM writing the answer's content
    -- carries run-to-run variance (0.10 mean abs soft delta) that no prompt
    fix removes.  So this variant generates NO content: the shipped baseline
    answer is kept sentence for sentence, and for each top-ranked span the
    baseline does not already cover, one verbatim clause is appended:

        Under Section 38 of Food Safety and Standards Act, 2006: "<the
        span's operative excerpt>" [3]

    The clause carries the figures the reference expects (the excerpt
    builder keeps every figure-bearing sentence) and its citation.  Nothing
    else changes, so the arm's variance is exactly zero and what it adds is
    checkable: every appended word is quoted from a span that was in the
    window.

    The appends are BUDGETED: the clauses may add at most ``budget_pct``%
    of the baseline's words (floor ``budget_floor`` words), which is what
    keeps the arm inside the v2 length gate.  A clause that does not fit is
    skipped and a shorter one later in the ranking may still be taken.
    Within the pool, candidates are ordered by their overlap with the
    QUESTION's own words (rank order breaks ties), so the small budget is
    spent on the clauses most likely to carry gold content.

    Returns the patched answer and the spans whose clauses were appended.
    """  # noqa: D205
    from app.rag.generation.abstention import is_abstention

    base = (baseline_answer or "").strip()
    if not base or is_abstention(base):
        # An abstention is a recorded answer; patching content into it would
        # fabricate an answer where the system said it had none.
        return base, []
    base_words = _content_words(base)
    question_words = _content_words(question_text)
    budget = max(budget_floor, int(len(base.split()) * budget_pct / 100))
    used = 0
    parts = [base]
    appended: list[Span] = []

    candidates: list[tuple[float, Span, str]] = []
    for span in selected_spans:
        excerpt = span.operative_excerpt or span.operative_rule
        if not excerpt or span.operative_is_heading:
            continue
        words = _content_words(excerpt)
        if not words:
            continue
        covered = len(words & base_words) / len(words)
        if covered >= cover_threshold:
            continue
        relevance = len(words & question_words) / len(words)
        candidates.append((relevance, span, excerpt))
    # Stable sort: rank order (the input order) breaks relevance ties.
    candidates.sort(key=lambda c: -c[0])

    for _, span, excerpt in candidates:
        if used >= budget or len(appended) >= max_clauses:
            break
        clause_words = len(excerpt.split()) + CLAUSE_FRAME_WORDS
        if used + clause_words > budget:
            continue  # too long; a shorter clause later in the ranking may fit
        where = f"Section {span.section} of {span.act}" if span.section else span.act
        cites = " ".join(f"[{i}]" for i in span.citation_indices)
        parts.append(f'Under {where}: "{excerpt}" {cites}'.strip())
        appended.append(span)
        used += clause_words
    return "\n".join(parts), appended


# ---------------------------------------------------------------------------
# Stage 2 -- contrastive adjudication
# ---------------------------------------------------------------------------

ADJUDICATION_SYSTEM = (
    "You are a statutory adjudicator for Indian food safety and environmental law. "
    "You are given a question and a numbered list of candidate statutory "
    "provisions extracted from the retrieved context. Compare every candidate "
    "against the facts of the question, then select the provision -- or, when "
    "the question's answer genuinely spans two provisions, the two provisions "
    "-- that govern. Answer in EXACTLY the format below and nothing else -- no "
    "preamble, no explanation outside the fields, no markdown.\n"
    "SPANS: <the id(s) of the governing candidate(s), comma separated, or NONE>\n"
    "RULE: <the operative words of the governing provision(s), copied verbatim>\n"
    "CONSEQUENCE: <one sentence: what the law requires or prohibits on these facts>\n"
    "ANSWER: <the final legal answer: the rule and the consequence, with its source numbers>\n"
    "CITATIONS: [<n>, <n>, ...]\n"
    "Rules:\n"
    "- Every id in SPANS must be one of the candidate ids (S1, S2, ...). At most three.\n"
    "- RULE must be copied word for word from the operative words of the "
    "selected candidate(s). Copy the WORDS ONLY -- do not include candidate "
    "ids, labels, brackets or quotation marks in RULE.\n"
    "- In ANSWER and CITATIONS use the bracketed NUMBERS from the candidate's "
    "'cite as' header (for example [3]). NEVER put a candidate id such as S12 "
    "inside brackets -- a bracketed token always means a source number.\n"
    "- ANSWER must state EVERY operative element of the governing provision "
    "that the question reaches: each tier of a penalty, each condition, each "
    "power, and each figure the provision fixes. A single sentence naming the "
    "governing rule is not an answer. If the reference provision fixes "
    "imprisonment AND a fine, the answer must give both; if it fixes a figure "
    "for one harm and a different figure for another, give both.\n"
    "- If no candidate governs, write SPANS: NONE and nothing else."
)

ADJUDICATION_USER = (
    "Facts:\n{question}\n"
    "\nCandidate statutory provisions extracted from the context:\n"
    "{spans}\n"
    "\nTask:\n"
    "1. Subsumption check: for each candidate, do ALL the facts of the question "
    "fall within it?\n"
    "2. Proviso check: does an exception or a penalty ladder change the result?\n"
    "3. Decision: identify EVERY candidate that answers ANY part of the "
    "question. Most questions need more than one: a power is usually defined "
    "in one place and applied in another, a penalty usually has a heading, a "
    "tier for one kind of harm and a different tier for another, and a "
    "licensing question usually splits between two authorities. Select up to "
    "three candidates.\n"
    "\nAnswer in the exact format specified. Here is a worked example of the "
    "required output:\n"
    "\n"
    "Facts: What is the punishment for selling contaminated food under section 5, "
    "and who may order the seizure?\n"
    "CANDIDATE S3 | Foo Act, Section 5 | cite as [7]\n"
    '    operative words: "(3) Any person who sells contaminated food shall be '
    "punishable with imprisonment for a term which may extend to six months and "
    'with a fine which may extend to five lakh rupees."\n'
    "CANDIDATE S8 | Foo Act, Section 9 | cite as [11]\n"
    '    operative words: "(1) The Commissioner may order the seizure of any '
    'article of food which appears to be unsafe."\n'
    "SPANS: S3, S8\n"
    "RULE: (3) Any person who sells contaminated food shall be punishable with "
    "imprisonment for a term which may extend to six months and with a fine "
    "which may extend to five lakh rupees. (1) The Commissioner may order the "
    "seizure of any article of food which appears to be unsafe.\n"
    "CONSEQUENCE: The punishment is imprisonment up to six months and a fine up "
    "to five lakh rupees, and seizure may be ordered by the Commissioner.\n"
    "ANSWER: Under section 5 of the Foo Act, a person who sells contaminated food "
    "is punishable with imprisonment for a term which may extend to six months "
    "and with a fine which may extend to five lakh rupees [7]; the seizure of "
    "unsafe food may be ordered by the Commissioner under section 9 [11].\n"
    "CITATIONS: [7] [11]\n"
    "\n"
    "Now answer for the facts and candidates above, in exactly that format."
)


def _span_block(span: Span) -> str:
    cites = " ".join(f"[{i}]" for i in span.citation_indices)
    # The candidate id is NOT bracketed: a bracketed token in this prompt
    # means "cite this source", and the adjudicator was copying the
    # bracketed candidate id straight into its answer instead of the source
    # number.
    lines = [f"CANDIDATE {span.span_id} | {span.authority_id} | cite as {cites}".rstrip()]
    lines.append(f'    operative words: "{span.operative_excerpt or span.operative_rule}"')
    if span.proviso_or_exception:
        lines.append(f'    proviso or exception: "{span.proviso_or_exception}"')
    if span.penalty_ladder:
        lines.append(f'    penalty ladder: "{span.penalty_ladder}"')
    if span.enforcement_authority:
        lines.append(f"    authority: {span.enforcement_authority}")
    return "\n".join(lines)


def build_adjudication_prompt(question: str, spans: list[Span], *, preselected: list[Span] | None = None) -> str:
    """The Stage 2 prompt.

    With ``preselected`` the governing spans were chosen deterministically
    (``select_deterministically``) and the model is told so: its job is ONLY
    to compose the answer from the selected candidates.  This is the
    variance fix -- selection was the part of the adjudication that two runs
    of the same arm could not agree on.
    """
    blocks = "\n".join(_span_block(s) for s in spans)
    if preselected is not None:
        ids = ", ".join(s.span_id for s in preselected)
        return (
            "Facts:\n{question}\n"
            "\nCandidate statutory provisions extracted from the context:\n"
            f"{blocks}\n"
            "\nTask:\n"
            "The governing candidates have ALREADY been selected for you by a "
            "deterministic ranker: "
            f"{ids}. Do NOT select or reject candidates, and do not use any "
            "candidate outside this list. Your job is ONLY to compose the "
            "answer: read the selected candidates' operative words, and state "
            "the rule and its consequence on these facts.\n"
            "\nAnswer in the exact format specified. Here is a worked example "
            "of the required output:\n"
            "\n"
            "Facts: What is the punishment for selling contaminated food under "
            "section 5, and who may order the seizure?\n"
            "CANDIDATE S3 | Foo Act, Section 5 | cite as [7]\n"
            '    operative words: "(3) Any person who sells contaminated food '
            "shall be punishable with imprisonment for a term which may extend "
            "to six months and with a fine which may extend to five lakh "
            'rupees."\n'
            "CANDIDATE S8 | Foo Act, Section 9 | cite as [11]\n"
            '    operative words: "(1) The Commissioner may order the seizure '
            'of any article of food which appears to be unsafe."\n'
            "SPANS: S3, S8\n"
            "RULE: (3) Any person who sells contaminated food shall be "
            "punishable with imprisonment for a term which may extend to six "
            "months and with a fine which may extend to five lakh rupees. "
            "(1) The Commissioner may order the seizure of any article of food "
            "which appears to be unsafe.\n"
            "CONSEQUENCE: The punishment is imprisonment up to six months and "
            "a fine up to five lakh rupees, and seizure may be ordered by the "
            "Commissioner.\n"
            "ANSWER: Under section 5 of the Foo Act, a person who sells "
            "contaminated food is punishable with imprisonment for a term "
            "which may extend to six months and with a fine which may extend "
            "to five lakh rupees [7]; the seizure of unsafe food may be "
            "ordered by the Commissioner under section 9 [11].\n"
            "CITATIONS: [7] [11]\n"
            "\n"
            "Now answer for the facts and candidates above, in exactly that "
            "format."
        ).format(question=question)
    return ADJUDICATION_USER.format(question=question, spans=blocks)


@dataclass
class Adjudication:
    span_ids: list[str] = field(default_factory=list)
    rule: str = ""
    consequence: str = ""
    answer: str = ""
    citations: list[int] = field(default_factory=list)
    parsed: bool = False
    raw: str = ""

    @property
    def none(self) -> bool:
        return not self.span_ids or all(s.upper() == "NONE" for s in self.span_ids)

    @property
    def span_id(self) -> str:
        """Single-id accessor, kept for reporting."""
        return ",".join(self.span_ids)


_FIELD_RE = re.compile(r"^\s*(SPANS?|RULE|CONSEQUENCE|ANSWER|CITATIONS)\s*:\s*(.*)$", re.IGNORECASE)
_CITE_RE = re.compile(r"\[(\d+)\]")
#: The adjudicator sometimes labels a copied clause with its candidate id,
#: e.g. '...under section 36; (S32); ...'.  The words are still verbatim, so
#: the labels are stripped before the check -- they are formatting, not
#: fabrication.
_SPAN_LABEL_RE = re.compile(r"\(\s*S\d+\s*\)", re.IGNORECASE)


def parse_adjudication(text: str) -> Adjudication:
    """Parse the fixed-format adjudication output.  Never raises.

    A field's value may continue on the following lines -- the adjudicator
    writes a two-sentence RULE and a multi-sentence ANSWER -- so the parser
    is field-scoped rather than line-scoped.  It stops at the next field
    header.  A response that never emits a field header (the model echoing
    the candidate list back) simply fails to parse, which is a rejection.
    """
    adj = Adjudication(raw=text or "")
    if not text:
        return adj
    current: str | None = None
    buffer: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _FIELD_RE.match(line)
        if match:
            current = match.group(1).upper()
            buffer.setdefault(current, []).append(match.group(2).strip())
            continue
        if current and line.strip():
            buffer[current].append(line.strip())
    values = {k: " ".join(v).strip() for k, v in buffer.items()}

    for key in ("SPAN", "SPANS"):
        if key in values:
            ids = [p.strip() for p in values[key].replace(";", ",").split(",") if p.strip()]
            adj.span_ids = ids[:3]
            break
    if "RULE" in values:
        adj.rule = values["RULE"].strip('"').strip()
    if "CONSEQUENCE" in values:
        adj.consequence = values["CONSEQUENCE"].strip('"').strip()
    if "ANSWER" in values:
        adj.answer = values["ANSWER"].strip('"').strip()
    if "CITATIONS" in values:
        adj.citations = [int(n) for n in _CITE_RE.findall(values["CITATIONS"])]
    if not adj.citations and adj.answer:
        # The model reliably puts its source numbers in ANSWER but often omits
        # the CITATIONS field; take them from the answer rather than reject.
        adj.citations = sorted({int(n) for n in _CITE_RE.findall(adj.answer)})
    adj.parsed = bool(adj.span_ids)
    return adj


# ---------------------------------------------------------------------------
# Stage 3 -- gated verification and fallback
# ---------------------------------------------------------------------------


def _repair_span_refs(adj: Adjudication, spans: list[Span]) -> Adjudication:
    """Repair candidate-id / source-number mix-ups in either direction.

    Two slips happen, both formatting rather than fabrication:

    * the answer cites the candidate it used -- "[Source: S32 and S31]" or
      "[S12]" -- where a source number belongs.  The span it names IS in the
      prompt, with its source numbers given in the candidate's 'cite as'
      header, so the reference is resolved to those numbers.
    * the SPANS field lists source numbers -- "SPANS: [3]" -- where a
      candidate id belongs, because the instruction "a bracketed token always
      means a source number" was over-applied.  The mapping is inverted.

    A reference that names no offered span is left alone, so the gate still
    rejects anything it cannot resolve.
    """
    by_id = {s.span_id: s for s in spans}
    reverse: dict[int, str] = {}
    for span in spans:
        for index in span.citation_indices:
            reverse.setdefault(index, span.span_id)

    if adj.span_ids and not any(sid in by_id for sid in adj.span_ids):
        mapped: list[str] = []
        for raw in re.findall(r"\d+", " ".join(adj.span_ids)):
            index = int(raw)
            if index in reverse and reverse[index] not in mapped:
                mapped.append(reverse[index])
        if mapped:
            adj.span_ids = mapped[:3]

    pattern = re.compile(r"\[[^\[\]]*?S\d+[^\[\]]*?\]", re.IGNORECASE)

    def resolve(match: re.Match[str]) -> str:
        ids = re.findall(r"S(\d+)", match.group(0), re.IGNORECASE)
        indices: list[int] = []
        for raw_id in ids:
            span = by_id.get(f"S{raw_id}")
            if span:
                indices.extend(span.citation_indices)
        if not indices:
            return match.group(0)
        return " ".join(f"[{i}]" for i in sorted(set(indices)))

    adj.answer = pattern.sub(resolve, adj.answer or "")
    adj.citations = sorted({int(n) for n in _CITE_RE.findall(adj.answer)})
    return adj


#: Markers of an adjudicator reporting that the rule is NOT in the candidates.
#: That is a retrieval failure, not an answer -- and it must not be emitted,
#: because it reads as an answer and scores like one.
_ABSENCE_MARKERS = (
    "not detailed",
    "not provided",
    "not specified",
    "not stated",
    "not captured",
    "not available",
    "no provision",
    "not present in",
    "cannot be determined",
)

#: Markers of the adjudicator reasoning about the OUTPUT FORMAT instead of
#: answering.  Two of six variance-run responses leaked ("But wait, the
#: output should be exactly that...", "But note: The ids are S1,S2"); one of
#: them even parsed as a span list.  The leak is a parse failure, not an
#: answer.
_FORMAT_LEAK_MARKERS = (
    "but wait",
    "but note",
    "the output should",
    "should be exactly",
    "maybe with a newline",
    "each field on a new line",
    "the format example",
    "comma separated",
    "at most three",
)


def verify(
    adj: Adjudication,
    spans: list[Span],
    window_indices: set[int],
    *,
    compose_mode: bool = False,
) -> tuple[bool, list[str]]:
    """Stage 3: accept the adjudication only if it verifies against the window.

    Multi-span: a question whose answer genuinely spans two provisions (Q007
    asks where a power is *defined* and what it *does*) selects both, and the
    rule is checked against the joined text of the selected spans.

    ``compose_mode`` is the deterministic-selection arm.  The model there
    only composes the answer from pre-selected candidates, so two checks are
    scoped differently:

    * the verbatim-RULE check is skipped -- the composer is not claiming a
      verbatim quote, and the anti-fabrication guarantee it needs is that
      every citation is a real window index and that the answer is built
      from a span that was offered;
    * the answer must cite AT LEAST ONE selected span, not every one -- the
      composer may legitimately use two of the three pre-selected
      candidates when the third does not bear on the question.

    In both modes the format-leak check runs against the EMITTED fields, not
    the raw output: the parser is field-scoped, so chain-of-thought preamble
    before the first field header never reaches the answer, and rejecting on
    the raw would discard recoverable outputs.
    """
    reasons: list[str] = []
    if not adj.parsed:
        return False, ["adjudication did not parse"]
    if adj.none:
        return False, ["adjudicator selected no candidate"]
    by_id = {s.span_id: s for s in spans}
    selected = [by_id[span_id.strip()] for span_id in adj.span_ids if span_id.strip() in by_id]
    unknown = [span_id for span_id in adj.span_ids if span_id.strip() not in by_id]
    if unknown:
        return False, [f"span ids not offered: {unknown}"]
    if not selected:
        return False, ["no usable span"]
    if not adj.answer:
        reasons.append("answer is empty")
    if not compose_mode:
        if not adj.rule:
            reasons.append("rule is empty")
        else:
            joined = _normalise(" ".join(s.text for s in selected))
            rule = _normalise(_SPAN_LABEL_RE.sub(" ", adj.rule))
            # Token-level rather than exact-substring: the corpus is OCR-noisy,
            # so a copied clause can differ by a stray character without the
            # adjudicator having paraphrased it.  85% of the rule's tokens must
            # be present in the selected span(s) -- one mangled character in a
            # nine-token clause loses 11%, which must not fail the check, while
            # a paraphrase reorders and drops the statutory vocabulary and does.
            rule_tokens = set(_tokens(rule))
            span_tokens = set(_tokens(joined))
            if not rule_tokens:
                reasons.append("rule is empty")
            else:
                overlap = len(rule_tokens & span_tokens) / len(rule_tokens)
                if overlap < 0.85:
                    reasons.append(f"rule is not verbatim from the selected span(s) ({overlap:.0%} of tokens)")
    bad_cites = [c for c in adj.citations if c not in window_indices]
    if bad_cites:
        reasons.append(f"citations not in the window: {bad_cites}")
    if not adj.citations:
        reasons.append("no citations declared")
    answer_cites = {int(n) for n in _CITE_RE.findall(adj.answer)}
    if not answer_cites:
        reasons.append("answer carries no [n] citation")
    elif compose_mode:
        # The composer may use a subset of the pre-selected candidates.
        cited_any = any(answer_cites & set(s.citation_indices) for s in selected)
        if not cited_any:
            reasons.append("answer cites none of the selected spans")
    else:
        for span in selected:
            if not (answer_cites & set(span.citation_indices)):
                reasons.append(f"answer does not cite the selected span {span.span_id}")
                break
    emitted = " ".join([adj.answer or "", adj.rule or "", adj.consequence or "", " ".join(adj.span_ids)]).lower()
    if any(marker in emitted for marker in _ABSENCE_MARKERS):
        reasons.append("adjudicator reported the rule is absent from the candidates")
    if any(marker in emitted for marker in _FORMAT_LEAK_MARKERS):
        reasons.append("output reasons about the format instead of answering")
    return (not reasons), reasons


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _load_cohort() -> dict[str, Any]:
    if not COHORT_FILE.exists():
        raise SystemExit(
            f"cohort not found: {COHORT_FILE}\nBuild it first:  python -m evaluation.build_generation_cohort"
        )
    return json.loads(COHORT_FILE.read_text(encoding="utf-8"))


def _llm_client() -> Any:
    """The whitelisted model, with the environment set for a live call.

    The environment is set HERE rather than at import time: this module is
    imported by the test suite, and flipping ``RAG_USE_STUB_LLM`` on import
    would change the behaviour of every other test in the same session.
    """
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["RAG_LLM_MODEL"] = MODEL
    os.environ["RAG_USE_STUB_LLM"] = "false"
    # Stage 2 is its own call; the structured reasoner must not also run.
    os.environ["ENABLE_STRUCTURED_REASONER"] = "false"

    from evaluation.llm_ssl_client import SSLBypassLLMClient

    client = SSLBypassLLMClient()
    if client.model != MODEL:
        raise SystemExit(f"model mismatch: expected {MODEL!r}, client has {client.model!r}")
    return client


def _score_one(
    row: dict[str, Any],
    spans: list[Span],
    adj: Adjudication,
    accepted: bool,
    reasons: list[str],
    questions: dict,
    *,
    fallback_on_regression: bool = False,
    regression_tolerance: float = REGRESSION_SOFT_TOLERANCE,
) -> dict[str, Any]:
    """Score the adjudicated answer and the baseline, and compare.

    Two selection rules, reported side by side:

    ``shipping``
        fall back to the baseline only when the adjudication fails
        verification.  This is the rule that could ship -- it needs no
        reference at runtime.
    ``experiment``
        additionally fall back when the adjudicated answer scores worse than
        the baseline by more than the tolerance.  It uses the reference, so it
        cannot ship; it exists so the experiment can ask whether the
        adjudication is worth taking at all.
    """
    q = questions[row["qid"]]
    baseline_answer = row["baseline"].get("answer") or ""
    adjudicated_answer = adj.answer if adj.answer else ""

    base_score = score_answer(baseline_answer, q.acceptable_conclusion or "", q.insufficient_evidence)
    adj_score = (
        score_answer(adjudicated_answer, q.acceptable_conclusion or "", q.insufficient_evidence)
        if adjudicated_answer
        else None
    )

    verified = accepted and bool(adjudicated_answer)
    regression = bool(
        adj_score is not None
        and adj_score["answer_correctness"] < base_score["answer_correctness"] - regression_tolerance
    )

    if verified and not (fallback_on_regression and regression):
        final_answer, source = adjudicated_answer, "adjudicated"
    else:
        final_answer, source = baseline_answer, "baseline_fallback"
    final_score = score_answer(final_answer, q.acceptable_conclusion or "", q.insufficient_evidence)

    return {
        "qid": row["qid"],
        "audit_action": row.get("audit_action"),
        "accepted": verified,
        "rejection_reasons": reasons,
        "regression_vs_baseline": regression,
        "source": source,
        "answer": final_answer,
        "adjudicated_answer": adjudicated_answer,
        "baseline_answer": baseline_answer,
        "selected_span_id": adj.span_id,
        "selected_rule": adj.rule,
        "selected_consequence": adj.consequence,
        "declared_citations": adj.citations,
        "final": {
            "binary_correct": int(final_score["binary_correct"]),
            "answer_correctness": final_score["answer_correctness"],
            "answer_coverage": final_score["answer_coverage"],
            "answer_jaccard": final_score["answer_jaccard"],
        },
        "baseline": {
            "binary_correct": int(base_score["binary_correct"]),
            "answer_correctness": base_score["answer_correctness"],
            "answer_coverage": base_score["answer_coverage"],
        },
        "adjudicated": (
            {
                "binary_correct": int(adj_score["binary_correct"]),
                "answer_correctness": adj_score["answer_correctness"],
                "answer_coverage": adj_score["answer_coverage"],
            }
            if adj_score
            else None
        ),
        "soft_delta_vs_baseline": round(final_score["answer_correctness"] - base_score["answer_correctness"], 4),
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"n": 0}
    accepted = [r for r in results if r["accepted"]]
    n = len(results)
    soft = [r["final"]["answer_correctness"] for r in results]
    base_soft = [r["baseline"]["answer_correctness"] for r in results]
    cov = [r["final"]["answer_coverage"] for r in results]
    base_cov = [r["baseline"]["answer_coverage"] for r in results]
    binary = sum(r["final"]["binary_correct"] for r in results)
    base_binary = sum(r["baseline"]["binary_correct"] for r in results)
    adj_binary = sum(r["adjudicated"]["binary_correct"] for r in results if r.get("adjudicated"))
    return {
        "n": n,
        "accepted": len(accepted),
        "rejected": n - len(accepted),
        "binary_correct": binary,
        "baseline_binary_correct": base_binary,
        "binary_net": binary - base_binary,
        "adjudicated_binary_correct": adj_binary,
        "soft_mean": round(sum(soft) / n, 4),
        "baseline_soft_mean": round(sum(base_soft) / n, 4),
        "soft_delta": round(sum(soft) / n - sum(base_soft) / n, 4),
        "coverage_mean": round(sum(cov) / n, 4),
        "baseline_coverage_mean": round(sum(base_cov) / n, 4),
        "coverage_delta": round(sum(cov) / n - sum(base_cov) / n, 4),
        "regressions_vs_baseline": sum(1 for r in results if r["soft_delta_vs_baseline"] < -0.0),
        "improvements_vs_baseline": sum(1 for r in results if r["soft_delta_vs_baseline"] > 0.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=0, help="0 = the whole cohort")
    parser.add_argument("--qids", default="", help="comma-separated subset")
    parser.add_argument("--max-spans", type=int, default=DEFAULT_MAX_SPANS)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true", help="Stage 1 only: extract spans, make no calls")
    parser.add_argument(
        "--selection",
        choices=("model", "deterministic", "patch"),
        default="model",
        help="who picks the governing spans and who writes the answer: the "
        "model (Stage 2, the original arm), the deterministic Stage 1 "
        "ranking with the model composing (--selection deterministic), or "
        "fully deterministic -- the baseline answer plus verbatim clauses "
        "appended from the top-ranked spans, with no LLM call at all "
        "(--selection patch).",
    )
    parser.add_argument(
        "--fallback-on-regression",
        action="store_true",
        help="also fall back to the baseline when the adjudicated answer scores worse "
        "(eval-only: it uses the reference, so it cannot ship as a runtime rule)",
    )
    parser.add_argument("--out", default=str(OUT_FILE))
    args = parser.parse_args()

    cohort = _load_cohort()
    questions = {q.question_id: q for q in load_questions()}
    rows = cohort["questions"]
    if args.qids:
        wanted = {q.strip() for q in args.qids.split(",") if q.strip()}
        rows = [r for r in rows if r["qid"] in wanted]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("no rows selected")
        return 1

    print(
        f"contrastive statutory reasoning -- {len(rows)} questions, model={MODEL}, selection={args.selection}",
        flush=True,
    )

    # ---- Stage 1 -------------------------------------------------------- #
    prepared: list[tuple[dict[str, Any], list[Span], set[int]]] = []
    recall_hits = 0
    for row in rows:
        citation_index = {
            str(c["chunk_id"]): int(c["index"])
            for c in row["context"].get("citations") or []
            if c.get("chunk_id") is not None and c.get("index") is not None
        }
        spans = extract_spans(
            row["question"],
            row["context"]["chunks"],
            citation_index,
            max_spans=args.max_spans,
        )
        window_indices = set(citation_index.values())
        prepared.append((row, spans, window_indices))

        # Stage 1 recall: is the span carrying the gold provision among the
        # candidates offered to the adjudicator?  This bounds the whole
        # experiment -- the adjudicator cannot pick a provision it was never
        # shown.
        gold_ids = set(row["context"].get("gold_admitted_chunk_ids") or [])
        offered_ids = {cid for s in spans for cid in s.chunk_ids}
        hit = bool(gold_ids & offered_ids)
        recall_hits += hit
        if args.dry_run:
            top = spans[0] if spans else None
            print(
                f"  {row['qid']}  groups={spans[0].group_total if spans else 0:<3} "
                f"offered={len(spans):<3} gold_offered={'Y' if hit else 'N'} "
                f"top={top.span_id if top else '-':<4} "
                f"sec={str(top.section) if top and top.section else '-':<5} "
                f"score={top.score if top else 0:>6.2f} "
                f"{' | ' + (top.operative_rule[:60] if top and top.operative_rule else '')}"
            )
    if args.dry_run:
        n_spans = [len(s) for _, s, _ in prepared]
        print(
            f"\nspans offered per question: mean {statistics.mean(n_spans):.1f} min {min(n_spans)} max {max(n_spans)}"
        )
        print(
            f"STAGE 1 RECALL (gold span offered): {recall_hits}/{len(prepared)} ({recall_hits / max(1, len(prepared)):.1%})"
        )
        return 0

    # ---- Stage 2 + 3 ---------------------------------------------------- #
    # The patch arm makes no LLM call, so no client is constructed for it.
    client = None if args.selection == "patch" else _llm_client()

    def one(item: tuple[dict[str, Any], list[Span], set[int]]) -> dict[str, Any]:
        row, spans, window_indices = item
        fb = args.fallback_on_regression
        if not spans:
            adj = Adjudication()
            return _score_one(
                row, spans, adj, False, ["no candidate spans extracted"], questions, fallback_on_regression=fb
            )
        if args.selection == "patch":
            # Fully deterministic: no LLM call, no generated content.  The
            # gate runs on the APPENDED clauses only -- the baseline's own
            # citations were already accepted by production and are not
            # this arm's responsibility.
            baseline_answer = row["baseline"].get("answer") or ""
            chosen = select_deterministically(spans, keep=PATCH_SPAN_POOL)
            patched, appended = compose_patch(baseline_answer, chosen, question_text=row["question"])
            if not appended:
                adj = Adjudication()
                return _score_one(
                    row,
                    spans,
                    adj,
                    False,
                    ["patch added no clause: baseline already covers the selected spans"],
                    questions,
                    fallback_on_regression=fb,
                )
            clause_text = "\n".join(
                f"Under {f'Section {s.section} of {s.act}' if s.section else s.act}: "
                f'"{s.operative_excerpt or s.operative_rule}" ' + " ".join(f"[{i}]" for i in s.citation_indices)
                for s in appended
            )
            gate_adj = Adjudication(
                span_ids=[s.span_id for s in appended],
                rule=" ".join(s.operative_excerpt or s.operative_rule for s in appended),
                answer=clause_text,
                citations=sorted({i for s in appended for i in s.citation_indices}),
            )
            gate_adj.parsed = True
            accepted, reasons = verify(gate_adj, spans, window_indices, compose_mode=True)
            score_adj = Adjudication(
                span_ids=[s.span_id for s in appended],
                rule=gate_adj.rule,
                answer=patched,
                citations=gate_adj.citations,
            )
            result = _score_one(row, spans, score_adj, accepted, reasons, questions, fallback_on_regression=fb)
            result["selection"] = "patch"
            result["appended_spans"] = [s.span_id for s in appended]
            return result
        chosen = select_deterministically(spans, keep=3) if args.selection == "deterministic" else None
        prompt = build_adjudication_prompt(row["question"], spans, preselected=chosen)
        try:
            resp = client.call(ADJUDICATION_SYSTEM, prompt)
        except Exception as exc:  # noqa: BLE001 — a failed call is a rejection, not a crash
            adj = Adjudication(raw=f"client error: {exc}")
            return _score_one(row, spans, adj, False, [f"call failed: {exc}"], questions, fallback_on_regression=fb)
        if resp.error:
            adj = Adjudication(raw=resp.error)
            return _score_one(
                row, spans, adj, False, [f"call failed: {resp.error}"], questions, fallback_on_regression=fb
            )
        adj = parse_adjudication(resp.text or "")
        if chosen is not None:
            # The selection was made deterministically; the model does not
            # get to change it, whatever it echoes in SPANS.
            adj.span_ids = [s.span_id for s in chosen]
        adj = _repair_span_refs(adj, spans)
        accepted, reasons = verify(adj, spans, window_indices, compose_mode=chosen is not None)
        result = _score_one(row, spans, adj, accepted, reasons, questions, fallback_on_regression=fb)
        result["selection"] = args.selection
        result["latency_s"] = round(float(resp.latency or 0), 2)
        result["model"] = resp.model
        result["raw_adjudication"] = (resp.text or "")[:1200]
        result["finish_reason"] = resp.finish_reason
        return result

    results: list[dict[str, Any]] = []
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futures = {ex.submit(one, item): item[0]["qid"] for item in prepared}
        for i, fut in enumerate(as_completed(futures), 1):
            qid = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001
                result = {"qid": qid, "error": f"{type(exc).__name__}: {exc}", "accepted": False}
            results.append(result)
            if "final" in result:
                f = result["final"]
                b = result["baseline"]
                print(
                    f"  [{i}/{len(prepared)}] {qid} {'ACCEPT' if result['accepted'] else 'reject'} "
                    f"src={result['source']:<19} soft {b['answer_correctness']:.4f}->{f['answer_correctness']:.4f} "
                    f"bin {b['binary_correct']}->{f['binary_correct']}",
                    flush=True,
                )
            else:
                print(f"  [{i}/{len(prepared)}] {qid} ERROR {result.get('error')}", flush=True)
            if args.delay:
                time.sleep(args.delay)

    results.sort(key=lambda r: str(r.get("qid")))

    # The SHIPPING rule, computed independently of the experiment rule: take
    # the adjudicated answer wherever it verified, and the baseline wherever it
    # did not.  This is the number that could actually be deployed.
    shipping = []
    for r in results:
        if "final" not in r:
            continue
        if r.get("accepted") and r.get("adjudicated_answer"):
            shipping.append({
                "binary": r["adjudicated"]["binary_correct"],
                "soft": r["adjudicated"]["answer_correctness"],
                "cov": r["adjudicated"]["answer_coverage"],
            })
        else:
            shipping.append({
                "binary": r["baseline"]["binary_correct"],
                "soft": r["baseline"]["answer_correctness"],
                "cov": r["baseline"]["answer_coverage"],
            })
    shipping_summary = {
        "binary": sum(s["binary"] for s in shipping),
        "soft": round(sum(s["soft"] for s in shipping) / len(shipping), 4) if shipping else None,
        "coverage": round(sum(s["cov"] for s in shipping) / len(shipping), 4) if shipping else None,
    }
    baseline_summary = {
        "binary": sum(r["baseline"]["binary_correct"] for r in results if "baseline" in r),
        "soft": round(
            sum(r["baseline"]["answer_correctness"] for r in results if "baseline" in r)
            / max(1, len([r for r in results if "baseline" in r])),
            4,
        ),
        "coverage": round(
            sum(r["baseline"]["answer_coverage"] for r in results if "baseline" in r)
            / max(1, len([r for r in results if "baseline" in r])),
            4,
        ),
    }

    summary = {
        "model": MODEL,
        "n": len(results),
        "max_spans": args.max_spans,
        "fallback_on_regression": bool(args.fallback_on_regression),
        "selection": args.selection,
        "overall": _aggregate(results),
        "shipping_rule": shipping_summary,
        "baseline": baseline_summary,
        "by_audit_action": {
            action: _aggregate([r for r in results if r.get("audit_action") == action])
            for action in sorted({str(r.get("audit_action")) for r in results})
        },
        "errors": [r for r in results if "error" in r],
    }
    out_path.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n" + "=" * 92)
    print("CONTRASTIVE STATUTORY REASONING -- RESULTS")
    print("=" * 92)
    o = summary["overall"]
    print(f"  n={o['n']}  accepted={o['accepted']}  rejected={o['rejected']}  selection={summary['selection']}")
    print(f"  binary      : {o['baseline_binary_correct']} -> {o['binary_correct']}  (net {o['binary_net']:+d})")
    print(f"  soft        : {o['baseline_soft_mean']:.4f} -> {o['soft_mean']:.4f}  ({o['soft_delta']:+.4f})")
    print(
        f"  coverage    : {o['baseline_coverage_mean']:.4f} -> {o['coverage_mean']:.4f}  ({o['coverage_delta']:+.4f})"
    )
    print(f"  improved / regressed vs baseline : {o['improvements_vs_baseline']} / {o['regressions_vs_baseline']}")
    ship = summary["shipping_rule"]
    base = summary["baseline"]
    print()
    print("  SHIPPING RULE (fall back only when verification fails; uses no reference):")
    print(
        f"    binary {base['binary']}->{ship['binary']}   soft {base['soft']:.4f}->{ship['soft']:.4f}"
        f"   coverage {base['coverage']:.4f}->{ship['coverage']:.4f}"
    )
    print(f"  EXPERIMENT RULE (also falls back on regression{' [ON]' if args.fallback_on_regression else ' [off]'})")
    print(
        f"    binary {base['binary']}->{o['binary_correct']}   soft {base['soft']:.4f}->{o['soft_mean']:.4f}"
        f"   coverage {base['coverage']:.4f}->{o['coverage_mean']:.4f}"
    )
    print("\n  by audit action (experiment rule):")
    for action, agg in summary["by_audit_action"].items():
        if not agg.get("n"):
            continue
        print(
            f"    {action:<24} n={agg['n']:<3} accepted={agg['accepted']:<3} "
            f"bin {agg['baseline_binary_correct']}->{agg['binary_correct']}  "
            f"soft {agg['soft_delta']:+.4f}  cov {agg['coverage_delta']:+.4f}"
        )
    print(f"\nwritten: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
