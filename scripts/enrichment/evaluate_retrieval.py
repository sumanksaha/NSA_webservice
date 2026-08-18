"""Phase 14 — baseline vs enriched retrieval evaluation (offline).

Compares retrieval quality of the **baseline** (dense cosine over the original
chunk vectors, replicating the production ``DenseRetriever`` path) against the
**enriched** pipeline (dense-first re-ranking with section boost when the
query names a section, and cross-reference expansion — the production config
recommended by the Phase 15 ablation; the keyword and summary credits were
measured net-negative / inert and are excluded).

``--ablate`` runs the Phase 15 ablation matrix (:data:`ABLATION_VARIANTS`)
measuring the marginal value of every enrichment feature on the same
dataset, writing ``reports/ablation_results.json``.

Determinism & safety:
- Runs entirely offline over ``backups/vector_store_*.json`` (the backup
  carries the real 12,819 dense vectors + payloads) — the live Qdrant cluster
  and its index are never touched.
- Ground truth is resolved from the enrichment store by matching the
  author-pinned gold phrases (``reports/eval_dataset.json``) against
  ``original_text`` — never from retrieval output.
- Memory: the 12,819 x 768 float32 matrix is ~39 MB; enrichment metadata is
  streamed from SQLite.  Well within the 8 GB budget.

Outputs:
- ``reports/evaluation_baseline.json``  per-query + aggregate metrics (dense)
- ``reports/evaluation_enriched.json``   per-query + aggregate metrics (enriched)
- ``reports/evaluation_summary.json``    head-to-head comparison + deltas

Usage::

    python scripts/enrichment/evaluate_retrieval.py \
        --dataset reports/eval_dataset.json \
        --source backup:backups/vector_store_fssai_legal_768_20260809_161941.json \
        --db instance/app.db \
        --report-dir reports

Optional ``--seed-db`` persists the dataset (with resolved gold chunk IDs)
into the ``rag_eval_dataset`` table so the existing EvalRunner framework can
reuse it.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

# Make the project root importable when run as a loose script (matches the
# other scripts in this directory).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SECTION_MARKER_RE = re.compile(r"(?:section|sec\.?|s\.?)\s+(\d{1,4}[A-Za-z]?)", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Minimal English stopwords — legal boilerplate and question scaffolding adds
#: no retrieval signal, and on a 13k-chunk corpus it drowns the enrichment
#: keywords (every chunk contains "act", "food", "shall", ...).
_STOPWORDS = frozenset(
    "a an the and or but if of to in on for with as is are was were be been being it its this that these those which who whom whose what when where why how not no do does did can could would should may might shall must have has had from by at into over under within without between among about after before during while per etc e.g i.e s t d m r c b",
)

RRF_K = 60.0


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------
def _unwrap(value) -> str:
    """Unwrap a provenance-tagged field (``{"value": ...}``) or pass str through."""
    if isinstance(value, dict):
        return value.get("value") or ""
    return value or ""


def normalize_text(text: str) -> str:
    """Collapse whitespace + lowercase — for phrase matching and tokens."""
    return re.sub(r"\s+", " ", (text or "")).lower().strip()


def tokens_of(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(normalize_text(text)) if t not in _STOPWORDS]


def cosine_topk(query_vec: np.ndarray, matrix: np.ndarray, ids: list[str], top_k: int) -> list[tuple[str, float]]:
    """Cosine similarity top-k over a row-normalised dense matrix."""
    q = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    scores = matrix @ q  # matrix rows are unit vectors
    order = np.argsort(-scores)[:top_k]
    return [(ids[i], float(scores[i])) for i in order]


def lexical_scores(
    query: str,
    doc_phrases: list[set[str]],
    idf: dict[str, float],
) -> np.ndarray:
    """Phrase-match lexical score per document.

    For each chunk, score = sum of IDF over the chunk's enrichment phrases
    that the query contains as substrings.  Phrase-level matching preserves
    the multi-word keyword units the deterministic extractor produced (a
    token-bag match of "improvement" + "notice" fires on unrelated chunks;
    the phrase "improvement notice" does not).
    """
    n = len(doc_phrases)
    scores = np.zeros(n, dtype=np.float64)
    q_norm = normalize_text(query)
    for i, phrases in enumerate(doc_phrases):
        if not phrases:
            continue
        s = 0.0
        for p in phrases:
            if p and p in q_norm:
                s += idf.get(p, 0.0)
        scores[i] = s
    return scores


def rrf_fuse(ranked: list[list[str]], k: float = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion over multiple ranked id lists."""
    fused: dict[str, float] = {}
    for rl in ranked:
        for rank, cid in enumerate(rl):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (rank + 1 + k)
    return fused


def recall_at(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    return len(gold & set(ranked[:k])) / len(gold)


def precision_at(ranked: list[str], gold: set[str], k: int) -> float:
    if not ranked[:k]:
        return 0.0
    return len(gold & set(ranked[:k])) / min(k, len(ranked[:k]))


def mrr(ranked: list[str], gold: set[str]) -> float:
    for i, cid in enumerate(ranked):
        if cid in gold:
            return 1.0 / (i + 1)
    return 0.0


def ndcg(ranked: list[str], gold: set[str], k: int = 10) -> float:
    """nDCG@k with binary relevance."""
    dcg = 0.0
    for i, cid in enumerate(ranked[:k]):
        if cid in gold:
            dcg += 1.0 / math.log2(i + 2)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return dcg / ideal if ideal > 0 else 0.0


def parse_query_section(query: str) -> str | None:
    """Return the section number a query explicitly cites, if any."""
    m = _SECTION_MARKER_RE.search(query)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_backup(path: str) -> tuple[np.ndarray, list[str], dict[str, dict]]:
    """Load dense vectors + payloads from a backup JSON.

    Returns ``(matrix, ids, payloads)`` where ``matrix`` is an (N, 768)
    float32 array of **unit** vectors, ``ids[i]`` the Qdrant point id, and
    ``payloads[cid]`` the raw payload dict.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    points = data["points"]
    n = len(points)
    dim = data.get("vector_size", 768)
    matrix = np.zeros((n, dim), dtype=np.float32)
    ids: list[str] = []
    payloads: dict[str, dict] = {}
    for i, p in enumerate(points):
        vec = p["vector"]
        dense = vec.get("dense") or [] if isinstance(vec, dict) else vec
        matrix[i] = np.asarray(dense, dtype=np.float32)
        cid = str(p.get("id"))
        ids.append(cid)
        payloads[cid] = p.get("payload") or {}
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-12)
    return matrix, ids, payloads


def load_enrichment(db_path: str) -> dict[str, dict]:
    """Stream enrichment records from SQLite into a compact per-chunk map.

    ``{chunk_id: {section, keywords: [..], cross_ref_targets: [..],
                  summary, text}}`` — only the fields the enriched retriever
    needs; the full JSON blobs are never held in memory together.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    out: dict[str, dict] = {}
    cur = conn.execute("SELECT chunk_id, data FROM chunk_enrichment WHERE status = 'VALIDATED'")
    for chunk_id, data in cur:
        rec = json.loads(data)
        xrefs = []
        for x in rec.get("cross_references") or []:
            t = x.get("target_chunk_id") if isinstance(x, dict) else None
            if t:
                xrefs.append(t)
        out[chunk_id] = {
            "section": (rec.get("legal_location") or {}).get("section", {}).get("value"),
            "keywords": list(rec.get("retrieval_keywords") or []),
            "cross_ref_targets": xrefs,
            # retrieval_summary is provenance-tagged (``{"value": ...}``) in the
            # v1.0 schema — unwrap it if present.
            "summary": _unwrap(rec.get("retrieval_summary")),
            "text": rec.get("original_text") or "",
            "document_id": (rec.get("_document") or {}).get("document_id"),
        }
    conn.close()
    return out


def _phrase_index(
    ids: list[str],
    enrichment: dict[str, dict],
    key: str,
    min_tokens: int = 1,
) -> tuple[list[set[str]], dict[str, float]]:
    """Per-chunk phrase sets + IDF map for ONE enrichment source.

    ``key`` is ``"keywords"`` or ``"summary"``; ``min_tokens`` filters
    keyword phrases to those with at least N tokens.  The deterministic
    keyword extractor emits many single-word legal headwords ("act",
    "food", "penalty") that appear in thousands of chunks — restricting to
    multi-word phrases isolates whether those generic headwords are noise.
    Raw chunk text is deliberately excluded so the dense path and each
    enrichment signal are measured independently.

    A query matches a chunk when the query contains one of the chunk's
    phrases as a substring (phrase-level matching, not token bags).  Phrase
    IDF weights rarer, more specific phrases higher.
    """
    n = len(ids)
    doc_phrases: list[set[str]] = []
    df: dict[str, int] = {}
    for cid in ids:
        enrich = enrichment.get(cid)
        if not enrich:
            doc_phrases.append(set())
            continue
        phrases: set[str] = set()
        if key == "keywords":
            for k in enrich["keywords"]:
                nk = normalize_text(k)
                if nk and len(nk.split()) >= min_tokens:
                    phrases.add(nk)
        else:  # summary — whole summary as one phrase unit
            nk = normalize_text(enrich["summary"])
            if nk:
                phrases.add(nk)
        doc_phrases.append(phrases)
        for p in phrases:
            df[p] = df.get(p, 0) + 1
    n_docs = max(n, 1)
    idf = {p: math.log((n_docs + 1) / (c + 1)) + 1.0 for p, c in df.items()}
    return doc_phrases, idf


def build_lexical_index(
    ids: list[str],
    enrichment: dict[str, dict],
) -> tuple[list[set[str]], dict[str, float], list[set[str]], dict[str, float]]:
    """Keyword + summary phrase indexes (see :func:`_phrase_index`)."""
    kw_phrases, kw_idf = _phrase_index(ids, enrichment, "keywords")
    sum_phrases, sum_idf = _phrase_index(ids, enrichment, "summary")
    return kw_phrases, kw_idf, sum_phrases, sum_idf


def build_multiword_keyword_index(
    ids: list[str],
    enrichment: dict[str, dict],
) -> tuple[list[set[str]], dict[str, float]]:
    """Keyword phrase index restricted to multi-word phrases (>= 2 tokens).

    Ablation probe for the generic-headword-noise hypothesis: if restricting
    keywords to phrases recovers a positive delta vs the single-word variant,
    the Phase 3 keyword extractor should drop single-word headwords.
    """
    return _phrase_index(ids, enrichment, "keywords", min_tokens=2)


# ---------------------------------------------------------------------------
# Retrievers
# ---------------------------------------------------------------------------
def baseline_retrieve(
    query_vec: np.ndarray,
    matrix: np.ndarray,
    ids: list[str],
    top_k: int = 10,
) -> list[str]:
    """Replicates the production DenseRetriever path: dense cosine only."""
    ranked = cosine_topk(query_vec, matrix, ids, top_k)
    return [cid for cid, _ in ranked]


#: Ablation feature flags — each maps to one enrichment capability so Phase
#: 15 can measure the marginal value of every increment.
FEATURE_KEYWORDS = "keywords"
FEATURE_SUMMARY = "summaries"
FEATURE_SECTION = "section"
FEATURE_CROSSREFS = "crossrefs"
ALL_FEATURES = frozenset({FEATURE_KEYWORDS, FEATURE_SUMMARY, FEATURE_SECTION, FEATURE_CROSSREFS})


#: Ablation variants: name -> enabled features.
ABLATION_VARIANTS: dict[str, frozenset[str]] = {
    "baseline": frozenset(),
    "keywords": frozenset({FEATURE_KEYWORDS}),
    "summaries": frozenset({FEATURE_SUMMARY}),
    "section": frozenset({FEATURE_SECTION}),
    "crossrefs": frozenset({FEATURE_CROSSREFS}),
    "keywords+section": frozenset({FEATURE_KEYWORDS, FEATURE_SECTION}),
    "keywords+crossrefs": frozenset({FEATURE_KEYWORDS, FEATURE_CROSSREFS}),
    "section+crossrefs": frozenset({FEATURE_SECTION, FEATURE_CROSSREFS}),
    "full": ALL_FEATURES,
    # Probe variant — same keyword feature but only >= 2-token phrases.
    "keywords_multiword": frozenset({FEATURE_KEYWORDS}),
}


def enriched_retrieve(
    query: str,
    query_vec: np.ndarray,
    matrix: np.ndarray,
    ids: list[str],
    enrichment: dict[str, dict],
    kw_phrases: list[set[str]],
    kw_idf: dict[str, float],
    sum_phrases: list[set[str]],
    sum_idf: dict[str, float],
    top_k: int = 10,
    features: frozenset[str] = ALL_FEATURES,
) -> list[str]:
    """Enriched retrieval: dense-first, enrichment-augmented re-ranking.

    Production design: the vector index stays authoritative (it carries the
    legal semantics); enrichment metadata augments it.  With all features
    enabled:

    1. Dense retrieval over a generous candidate pool (top ``8*top_k``).
    2. Cross-reference expansion: chunks referenced by pool members enter the
       pool (multi-hop recall — retrieval can now reach provisions that the
       query never mentions but which the corpus itself links to).
    3. Re-rank the pool by ``dense_score`` + enrichment credits:
       - ``0.10`` per matched enrichment keyword phrase (lexical tie-break),
       - ``0.10`` per matched retrieval-summary phrase,
       - ``0.20`` when the query explicitly cites a section and the chunk is
         attributed to that section (the section attribution the enrichment
         store added is used as a real retrieval aid),
       - ``0.05`` per incoming cross-reference edge from any pool member
         (capped at 3 edges = 0.15; keeps expanded chunks — which start at
         dense score 0.0 — competitive without ever overtaking a decent
         direct dense hit, so the crossrefs variant is a conservative
         lower bound on expansion value).

    ``features`` gates each credit (Phase 15 ablation).  With an empty
    feature set this is exactly dense retrieval — so ablation variants are
    directly comparable to the production baseline.
    """
    dense_ranked = cosine_topk(query_vec, matrix, ids, top_k * 8)
    dense_ids = [cid for cid, _ in dense_ranked]
    dense_score = {cid: s for cid, s in dense_ranked}

    # 1) Cross-reference expansion (feature-gated)
    pool = list(dense_ids)
    incoming: dict[str, int] = {cid: 0 for cid in pool}
    if FEATURE_CROSSREFS in features:
        for cid in dense_ids:
            for t in enrichment.get(cid, {}).get("cross_ref_targets", []):
                if t not in dense_score:
                    pool.append(t)
                    dense_score[t] = 0.0  # expanded, no dense evidence yet
                incoming[t] = incoming.get(t, 0) + 1

    # 2) Lexical phrase match over the pool only (cheap, targeted)
    idx_of = {cid: i for i, cid in enumerate(ids)}
    lex_by_id: dict[str, float] = {}
    if FEATURE_KEYWORDS in features:
        lex = lexical_scores(query, kw_phrases, kw_idf)
        for cid in pool:
            if cid in idx_of:
                lex_by_id[cid] = lex_by_id.get(cid, 0.0) + float(lex[idx_of[cid]])
    if FEATURE_SUMMARY in features:
        lex = lexical_scores(query, sum_phrases, sum_idf)
        for cid in pool:
            if cid in idx_of:
                lex_by_id[cid] = lex_by_id.get(cid, 0.0) + float(lex[idx_of[cid]])
    lex_norm = max(lex_by_id.values()) if lex_by_id else 0.0

    cited = parse_query_section(query)

    final: dict[str, float] = {}
    for cid in pool:
        score = dense_score[cid]
        if lex_norm:
            score += 0.10 * (lex_by_id.get(cid, 0.0) / lex_norm)
        if FEATURE_SECTION in features and cited and str(enrichment.get(cid, {}).get("section") or "") == str(cited):
            score += 0.20
        # Explicitly gated: ``incoming`` is only populated by the expansion
        # block above, but gate here too so no future edit can silently apply
        # cross-reference credit to a variant without FEATURE_CROSSREFS.
        if FEATURE_CROSSREFS in features and incoming.get(cid, 0):
            score += 0.05 * min(incoming[cid], 3)
        final[cid] = score

    return sorted(final, key=final.get, reverse=True)[:top_k]


# ---------------------------------------------------------------------------
# Gold resolution
# ---------------------------------------------------------------------------
def _gold_norm(text: str) -> str:
    """Whitespace-normalise + strip quotes/parens for tolerant gold matching.

    Corpus text contains OCR/typographic noise around quoted legal terms
    (``(o) \"food business operator\" in relation to...``) — stripping
    punctuation makes the author-pinned phrases match the same content
    without weakening the document restriction.
    """
    return re.sub(r"[\"'()\[\]{}.,;:]", "", normalize_text(text))


def resolve_gold(
    q: dict,
    enrichment: dict[str, dict],
) -> tuple[set[str], list[str]]:
    """Resolve a question's gold phrases to chunk IDs.

    Matches each phrase against ``original_text`` (whitespace-normalised and
    punctuation-stripped) restricted to the question's ``document_id``.  All
    chunks containing a phrase join the gold set (a distinctive answer phrase
    may appear in more than one chunk).  Returns ``(gold_ids,
    unmatched_phrases)`` where unmatched phrases could not be found anywhere
    in the target document.
    """
    target_doc = q.get("document_id")
    phrases = q.get("gold_phrases", []) or []
    norm_phrases = [_gold_norm(p) for p in phrases]

    # Build the per-document text index once (shared across questions by the
    # caller is not possible here, so this is O(corpus) per call — acceptable
    # for a one-shot eval harness; the corpus is ~13k records).
    docs: dict[str, list[tuple[str, str]]] = {}
    for cid, e in enrichment.items():
        if not e.get("text"):
            continue
        docs.setdefault(e.get("document_id"), []).append((cid, e["text"]))

    # Collect ALL chunks containing each phrase (a distinctive answer phrase
    # may legitimately appear in more than one chunk); do NOT consume phrases
    # after the first match, otherwise gold becomes incomplete and dependent
    # on dict iteration order.
    gold: set[str] = set()
    matched: list[bool] = [False] * len(norm_phrases)
    for cid, text in docs.get(target_doc, []):
        norm = _gold_norm(text)
        for i, np_ in enumerate(norm_phrases):
            if np_ and np_ in norm:
                gold.add(cid)
                matched[i] = True

    unmatched = [p for i, p in enumerate(phrases) if not matched[i]]
    return gold, unmatched


# ---------------------------------------------------------------------------
# Metrics + reporting
# ---------------------------------------------------------------------------
def evaluate_question(
    q: dict,
    gold: set[str],
    ranked: list[str],
) -> dict:
    return {
        "id": q["id"],
        "archetype": q.get("archetype"),
        "difficulty": q.get("difficulty"),
        "gold_chunk_count": len(gold),
        "recall_at_5": recall_at(ranked, gold, 5),
        "recall_at_10": recall_at(ranked, gold, 10),
        "precision_at_5": precision_at(ranked, gold, 5),
        "mrr": mrr(ranked, gold),
        "ndcg_at_10": ndcg(ranked, gold, 10),
        "ranked_head": ranked[:5],
    }


def aggregate(rows: list[dict]) -> dict:
    n = max(len(rows), 1)
    return {
        "queries": len(rows),
        "recall_at_5": sum(r["recall_at_5"] for r in rows) / n,
        "recall_at_10": sum(r["recall_at_10"] for r in rows) / n,
        "precision_at_5": sum(r["precision_at_5"] for r in rows) / n,
        "mrr": sum(r["mrr"] for r in rows) / n,
        "ndcg_at_10": sum(r["ndcg_at_10"] for r in rows) / n,
    }


def by_archetype(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        a = r["archetype"] or "other"
        out.setdefault(a, []).append(r)
    return {a: aggregate(rs) for a, rs in sorted(out.items())}


# ---------------------------------------------------------------------------
# Embedding (real model, cached)
# ---------------------------------------------------------------------------
class _Embedder:
    """Lazy sentence-transformers embedder (cached on disk)."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        return self._load().encode(texts, normalize_embeddings=True, show_progress_bar=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_eval(
    dataset_path: str,
    source: str,
    db_path: str,
    report_dir: str,
    seed_db: bool = False,
    model_name: str = "sentence-transformers/all-mpnet-base-v2",
) -> dict:
    t0 = time.monotonic()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)
    questions = dataset["questions"]

    assert source.startswith("backup:"), "offline eval currently supports --source backup:<path>"
    backup_path = source.split(":", 1)[1]
    matrix, ids, _payloads = load_backup(backup_path)

    enrichment = load_enrichment(db_path)

    kw_phrases, kw_idf, sum_phrases, sum_idf = build_lexical_index(ids, enrichment)

    # Resolve gold
    gold_by_q: dict[str, tuple[set[str], list[str]]] = {}
    for q in questions:
        gold, unmatched = resolve_gold(q, enrichment)
        gold_by_q[q["id"]] = (gold, unmatched)

    # Embed queries
    embedder = _Embedder(model_name)
    qvecs = embedder.embed([q["question"] for q in questions])

    baseline_rows: list[dict] = []
    enriched_rows: list[dict] = []
    skipped: list[str] = []
    for i, q in enumerate(questions):
        gold, unmatched = gold_by_q[q["id"]]
        if not gold:
            skipped.append(q["id"])
            continue
        b_ranked = baseline_retrieve(qvecs[i], matrix, ids)
        e_ranked = enriched_retrieve(
            q["question"],
            qvecs[i],
            matrix,
            ids,
            enrichment,
            kw_phrases,
            kw_idf,
            sum_phrases,
            sum_idf,
            # Production recommendation from the Phase 15 ablation: the
            # section boost + cross-reference expansion are the two features
            # with net-positive deltas; the keyword credit and (deterministic-
            # empty) summary credit measured negative / inert and are excluded.
            features=frozenset({FEATURE_SECTION, FEATURE_CROSSREFS}),
        )
        baseline_rows.append(evaluate_question(q, gold, b_ranked))
        enriched_rows.append(evaluate_question(q, gold, e_ranked))

    baseline_agg = aggregate(baseline_rows)
    enriched_agg = aggregate(enriched_rows)

    # Honest attribution: the 3 section-citing questions (q17-19) were added
    # specifically to exercise the section-boost feature.  Report the core
    # set separately so the headline delta is not inflated by dataset
    # construction.
    core_ids = {q["id"] for q in questions if not q.get("section_citing")}

    def _split(rows: list[dict]) -> tuple[dict, dict]:
        core = [r for r in rows if r["id"] in core_ids]
        citing = [r for r in rows if r["id"] not in core_ids]
        return aggregate(core) if core else {}, aggregate(citing) if citing else {}

    baseline_core, baseline_citing = _split(baseline_rows)
    enriched_core, enriched_citing = _split(enriched_rows)

    summary = {
        "dataset": dataset.get("dataset_name"),
        "source": source,
        "embedding_model": model_name,
        "queries_total": len(questions),
        "queries_evaluated": len(baseline_rows),
        "queries_skipped_no_gold": skipped,
        "baseline": baseline_agg,
        "enriched": enriched_agg,
        "delta_enriched_minus_baseline": {
            k: round(enriched_agg[k] - baseline_agg[k], 4)
            for k in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")
        },
        "core_queries": {
            "ids": sorted(core_ids),
            "baseline": baseline_core,
            "enriched": enriched_core,
            "delta_enriched_minus_baseline": {
                k: round(enriched_core[k] - baseline_core[k], 4)
                for k in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")
            }
            if baseline_core
            else {},
        },
        "section_citing_queries": {
            "baseline": baseline_citing,
            "enriched": enriched_citing,
            "delta_enriched_minus_baseline": {
                k: round(enriched_citing[k] - baseline_citing[k], 4)
                for k in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")
            }
            if baseline_citing
            else {},
        },
        "baseline_by_archetype": by_archetype(baseline_rows),
        "enriched_by_archetype": by_archetype(enriched_rows),
        "elapsed_seconds": round(time.monotonic() - t0, 1),
    }

    with open(report_dir / "evaluation_baseline.json", "w", encoding="utf-8") as f:
        json.dump(
            {"aggregate": baseline_agg, "by_archetype": by_archetype(baseline_rows), "queries": baseline_rows},
            f,
            indent=2,
        )
    with open(report_dir / "evaluation_enriched.json", "w", encoding="utf-8") as f:
        json.dump(
            {"aggregate": enriched_agg, "by_archetype": by_archetype(enriched_rows), "queries": enriched_rows},
            f,
            indent=2,
        )
    with open(report_dir / "evaluation_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if seed_db:
        _seed_eval_dataset(db_path, dataset, gold_by_q)

    return summary


def run_ablation(
    dataset_path: str,
    source: str,
    db_path: str,
    report_dir: str,
    model_name: str = "sentence-transformers/all-mpnet-base-v2",
) -> dict:
    """Phase 15 — ablation: measure each enrichment increment's marginal value.

    Evaluates every variant in :data:`ABLATION_VARIANTS` (baseline, each
    single feature, promising pairs, and full) on the SAME dataset and gold.
    Writes ``reports/ablation_results.json`` with per-variant aggregate
    metrics and deltas vs the dense baseline.
    """
    import time

    t0 = time.monotonic()
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    with open(dataset_path, encoding="utf-8") as f:
        dataset = json.load(f)
    questions = dataset["questions"]

    assert source.startswith("backup:")
    matrix, ids, _payloads = load_backup(source.split(":", 1)[1])

    enrichment = load_enrichment(db_path)
    kw_phrases, kw_idf, sum_phrases, sum_idf = build_lexical_index(ids, enrichment)

    gold_by_q: dict[str, set[str]] = {}
    skipped: list[str] = []
    for q in questions:
        gold, _unmatched = resolve_gold(q, enrichment)
        gold_by_q[q["id"]] = gold
        if not gold:
            skipped.append(q["id"])

    embedder = _Embedder(model_name)
    qvecs = embedder.embed([q["question"] for q in questions])

    # Multi-word-keyword probe index (Phase 15 hypothesis test): generic
    # single-word headwords are the suspected source of the negative keyword
    # delta — measure the >= 2-token phrase index separately.
    mw_phrases, mw_idf = build_multiword_keyword_index(ids, enrichment)

    def _retrieve(variant: str, i: int, q: dict, features: frozenset[str]) -> list[str]:
        if not features:
            return baseline_retrieve(qvecs[i], matrix, ids)
        # Multi-word variant swaps in the filtered index for keywords
        kw_p, kw_i = (mw_phrases, mw_idf) if variant == "keywords_multiword" else (kw_phrases, kw_idf)
        return enriched_retrieve(
            q["question"],
            qvecs[i],
            matrix,
            ids,
            enrichment,
            kw_p,
            kw_i,
            sum_phrases,
            sum_idf,
            features=features,
        )

    results: dict[str, dict] = {}
    per_query: dict[str, list[dict]] = {}
    for variant, features in ABLATION_VARIANTS.items():
        rows: list[dict] = []
        for i, q in enumerate(questions):
            gold = gold_by_q[q["id"]]
            if not gold:
                continue
            ranked = _retrieve(variant, i, q, features)
            rows.append(evaluate_question(q, gold, ranked))
        results[variant] = aggregate(rows)
        per_query[variant] = rows

    baseline = results["baseline"]
    deltas = {
        variant: {
            k: round(results[variant][k] - baseline[k], 4)
            for k in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr", "ndcg_at_10")
        }
        for variant in results
    }

    out = {
        "dataset": dataset.get("dataset_name"),
        "queries_total": len(questions),
        "queries_evaluated": len(per_query["baseline"]),
        "queries_skipped_no_gold": skipped,
        "variants": results,
        "delta_vs_baseline": deltas,
        "by_archetype": {variant: by_archetype(per_query[variant]) for variant in results},
        "elapsed_seconds": round(time.monotonic() - t0, 1),
    }
    with open(report_dir / "ablation_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    for variant in results:
        deltas[variant]
    return out


def _seed_eval_dataset(db_path: str, dataset: dict, gold_by_q: dict[str, tuple[set[str], list[str]]]) -> None:
    """Persist the authored dataset into ``rag_eval_dataset`` (idempotent)."""
    import uuid

    from app import create_app
    from app.extensions import db
    from app.models.rag import RAGEvalDataset

    app = create_app()
    with app.app_context():
        existing = {r.query: r for r in db.session.query(RAGEvalDataset).filter_by(is_active=True).all()}
        added = updated = 0
        for q in dataset["questions"]:
            gold, _ = gold_by_q.get(q["id"], (set(), []))
            rec = existing.get(q["question"])
            if rec is None:
                rec = RAGEvalDataset(
                    id=str(uuid.uuid4()),
                    name=dataset.get("dataset_name", "fssai_enrichment_eval_v1"),
                    query=q["question"],
                    query_type=q.get("archetype", "general_qa"),
                    expected_answer="; ".join(q.get("gold_phrases", [])),
                    expected_section=q.get("section"),
                    expected_citations=sorted(gold),
                    difficulty=q.get("difficulty", "medium"),
                    is_active=True,
                )
                db.session.add(rec)
                added += 1
            else:
                # Refresh gold/section on re-seed (idempotent upsert)
                rec.expected_section = q.get("section")
                rec.expected_citations = sorted(gold)
                rec.expected_answer = "; ".join(q.get("gold_phrases", []))
                updated += 1
        db.session.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 14 retrieval evaluation")
    ap.add_argument("--dataset", default="reports/eval_dataset.json")
    ap.add_argument("--source", default="backup:backups/vector_store_fssai_legal_768_20260809_161941.json")
    ap.add_argument("--db", default="instance/app.db")
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--model", default="sentence-transformers/all-mpnet-base-v2")
    ap.add_argument("--seed-db", action="store_true", help="persist dataset into rag_eval_dataset")
    ap.add_argument(
        "--ablate",
        action="store_true",
        help="run the Phase 15 ablation matrix instead of the baseline-vs-enriched eval",
    )
    args = ap.parse_args()

    if args.ablate:
        run_ablation(
            dataset_path=args.dataset,
            source=args.source,
            db_path=args.db,
            report_dir=args.report_dir,
            model_name=args.model,
        )
        return

    run_eval(
        dataset_path=args.dataset,
        source=args.source,
        db_path=args.db,
        report_dir=args.report_dir,
        seed_db=args.seed_db,
        model_name=args.model,
    )


if __name__ == "__main__":
    main()
