"""Apply Landing-1 food-view edits to app/rag/tasks.py with explicit UTF-8."""
import sys

P = "app/rag/tasks.py"
raw = open(P, "rb").read()
crlf = b"\r\n" in raw
src = raw.decode("utf-8")
if crlf:
    src = src.replace("\r\n", "\n")
assert "â€" not in src, "file already mojibake"


def sub(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {n}")
    src = src.replace(old, new)


# --- A: single construction site + fetch width off the view -----------------
sub(
    """    food_understanding = _retrieval_food_understanding(query)

    # Stage 2 — fetch evidence through the hybrid retriever with the
    # §12.1 cache in front of it.  When the food view wants a specific
    # provision (standard/limit), a wider candidate pool is fetched so the
    # legal-aware reranker has clause siblings to reconstruct from.
    fetch_k = top_k
    if food_understanding is not None and (cfg.food_legal_rerank or cfg.food_parent_reconstruct):
        fetch_k = max(top_k, 30)
""",
    """    food_view = _retrieval_food_view(query)

    # Stage 2 — fetch evidence through the hybrid retriever with the
    # §12.1 cache in front of it. The food view resolves its own pool
    # width (wider when it wants clause siblings to reconstruct from).
    fetch_k = food_view.fetch_width(top_k) if food_view is not None else top_k
""",
    "A",
)

# --- B: stage 2b consumes the view -----------------------------------------
sub(
    """    # Stage 2b — legal-aware reranking + validation + fallback (food-intent
    # flags; each stage is error-isolated so a failure degrades to the
    # hybrid ranking exactly as before).
    trace: dict[str, Any] | None = None
    fallback_rounds_used = 0
    if food_understanding is not None:
        result, trace, fallback_rounds_used = _retrieval_food_rerank_validate(
            query,
            result,
            food_understanding,
            top_k=top_k,
            collection_name=collection_name,
            merged_filters=merged_filters,
            query_type=query_type,
            legal_qt=legal_qt,
            identifier=identifier,
            identifier_query=identifier_query,
            cache=cache,
        )
""",
    """    # Stage 2b — food-intent reranking + validation + fallback. The view
    # carries the fetch context, so the stage asks one narrow question.
    trace: dict[str, Any] | None = None
    fallback_rounds_used = 0
    if food_view is not None:
        from app.rag.retrieval.food_view import FoodFetchContext, food_stage_2b

        result, trace, fallback_rounds_used = food_stage_2b(
            food_view,
            result,
            FoodFetchContext(
                top_k=top_k,
                collection_name=collection_name,
                merged_filters=merged_filters,
                query_type=query_type,
                legal_qt=legal_qt,
                identifier=identifier,
                identifier_query=identifier_query,
                form_query=None,
                cache=cache,
            ),
            _retrieval_fetch,
        )
""",
    "B",
)

# --- C: output dict reads the view -----------------------------------------
sub(
    """    if food_understanding is not None:
        out["food_understanding"] = food_understanding.to_dict()
""",
    """    if food_view is not None:
        out["food_understanding"] = food_view.to_result_dict()
""",
    "C",
)

# --- D: replace the old parser helper with the view construction -----------
sub(
    '''def _retrieval_food_understanding(query: str) -> Any | None:
    """Food-commodity view of the query (RAG_FOOD_INTENT_ENABLED), or None.

    Error-isolated: a parse failure degrades to the pre-existing pipeline.
    """
    if not cfg.food_intent_enabled:
        return None
    try:
        from app.rag.retrieval.food_query_understanding import FoodQueryUnderstanding

        return FoodQueryUnderstanding.from_query(query)
    except Exception as exc:
        logger.warning("_retrieval_food_understanding failed: %s", exc)
        return None
''',
    '''def _retrieval_food_view(query: str) -> Any | None:
    """Food-commodity view of the query (RAG_FOOD_INTENT_ENABLED), or None.

    Single construction site: the query is parsed once and every food
    stage reads its answers off the returned view. Error-isolated: a
    parse failure degrades to the pre-existing pipeline.
    """
    from app.rag.retrieval.food_view import FoodView

    try:
        return FoodView.for_query(query)
    except Exception as exc:
        logger.warning("_retrieval_food_view failed: %s", exc)
        return None
''',
    "D",
)

# --- E: delete the ~190-line stage body (moved to food_view) ---------------
s = src.index("def _retrieval_food_rerank_validate(")
e = src.index("def _retrieval_understand_query(")
if src[s:e].count("\n    ") < 100:
    raise SystemExit("E: unexpected stage body size")
src = src[:s] + src[e:]

# --- F: keep _point_to_chunk (used by food_view.siblings) ------------------
assert "_point_to_chunk" in src
assert "_retrieval_food_understanding" not in src
assert "_retrieval_food_rerank_validate" not in src

out = src.replace("\n", "\r\n") if crlf else src
with open(P, "wb") as fh:
    fh.write(out.encode("utf-8"))

print("patched OK")
print("lines:", src.count("\n"))
