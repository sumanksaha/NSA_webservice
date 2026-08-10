"""HTTP endpoints for the RAG ingestion pipeline (Agent A, Phase 5).

- ``GET /api/rag/health`` — pipeline health probe (public — no auth required).
- ``POST /api/rag/ingest`` — ingest one document (raw text OR file path).
- ``POST /api/rag/ingest/corpus`` — ingest every supported file in a corpus
  directory.

Ingestion routes are auth-protected (the global ``require_login`` gate) and
return 503 when the RAG module is disabled (``RAG_ENABLED=false``), mirroring
the AI-assistant route convention.  All heavy work is delegated to the plain
entry points in ``app/rag/ingestion.py`` (``run_ingest_document`` /
``ingest_corpus_dir``), which build the production-default pipeline via
``make_ingestion_pipeline`` (Day 9 ``DocumentClassifier`` always wired;
full Phase 2 enrichment when ``RAG_FULL_ENRICHMENT`` is set).
"""

from __future__ import annotations

import logging

from flask import current_app, jsonify, request

from app.rag import rag_bp

logger = logging.getLogger(__name__)


def _rag_enabled() -> bool:
    """Whether the RAG module is enabled (``RAG_ENABLED`` config)."""
    return bool(current_app.config.get("RAG_ENABLED", True))


@rag_bp.route("/health")
def health():
    """RAG pipeline health probe (public — no auth required)."""
    return jsonify({"status": "ok", "phase": "5", "phase_name": "ingestion_api"})


@rag_bp.route("/ingest", methods=["POST"])
def ingest():
    """Ingest a single legal document (raw text OR a corpus file path).

    Request JSON:
        ``{"text": str}`` — ingest raw text.
        ``{"source": "/path/to/file.pdf"}`` — ingest a supported corpus file
            (pdf/docx/txt) from the server filesystem.
        Optional ``document`` dict — caller-provided metadata that always
            wins over extracted/classified values.
        Optional ``full_enrichment`` bool — override ``RAG_FULL_ENRICHMENT``
            for this request (None = resolve the flag normally).

    If both ``text`` and ``source`` are provided, ``source`` takes
    precedence (it is checked first by ``run_ingest_document``).

    Response JSON: the ``IngestedDocumentResult`` dict (``ok`` indicates
    whether the document was fully indexed; ``errors`` list any failures).
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    text = payload.get("text")
    source = payload.get("source")
    if not text and not source:
        return jsonify({"error": "Provide either 'text' or 'source'."}), 400

    document = payload.get("document") or {}
    if not isinstance(document, dict):
        return jsonify({"error": "document must be an object."}), 400
    full_enrichment = payload.get("full_enrichment")
    if full_enrichment is not None and not isinstance(full_enrichment, bool):
        return jsonify({"error": "full_enrichment must be a boolean."}), 400

    from app.rag.ingestion import make_ingestion_pipeline, run_ingest_document

    try:
        pipeline = make_ingestion_pipeline(full_enrichment=full_enrichment)
        result = run_ingest_document(source or text, document=document, pipeline=pipeline)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with details
        logger.error("RAG ingest failed: %s", exc)
        return jsonify({"error": f"Ingestion failed: {exc}"}), 500
    return jsonify(result)


@rag_bp.route("/ingest/corpus", methods=["POST"])
def ingest_corpus():
    """Ingest every supported file under a corpus directory (non-recursive).

    Request JSON:
        ``{"corpus_dir": "/path/to/corpus"}`` — directory to scan for
            pdf/docx/txt files.
        Optional ``document`` dict / ``full_enrichment`` bool (as above).

    Response JSON: the corpus summary dict (``total`` / ``indexed`` /
    ``duplicates`` / ``failed`` / ``results``).
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    corpus_dir = payload.get("corpus_dir")
    if not corpus_dir or not isinstance(corpus_dir, str) or not corpus_dir.strip():
        return jsonify({"error": "corpus_dir must be a non-empty string."}), 400

    document = payload.get("document") or {}
    if not isinstance(document, dict):
        return jsonify({"error": "document must be an object."}), 400
    full_enrichment = payload.get("full_enrichment")
    if full_enrichment is not None and not isinstance(full_enrichment, bool):
        return jsonify({"error": "full_enrichment must be a boolean."}), 400

    from app.rag.ingestion import ingest_corpus_dir, make_ingestion_pipeline

    try:
        pipeline = make_ingestion_pipeline(full_enrichment=full_enrichment)
        summary = ingest_corpus_dir(corpus_dir, document=document, pipeline=pipeline)
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with details
        logger.error("RAG corpus ingest failed: %s", exc)
        return jsonify({"error": f"Corpus ingestion failed: {exc}"}), 500
    return jsonify(summary)



@rag_bp.route("/generate", methods=["POST"])
def generate():
    """Grounded RAG generation endpoint (Phase 2).

    Request JSON:
        query (str, required): The user legal question.
        chunks (list[dict], optional): Pre-retrieved chunks from a
            prior retrieve_task. If omitted, retrieval runs first.
        query_type (str, optional): Overridden query classification.
        top_k (int, optional): Chunks to retrieve (default 10).
        collection_name (str, optional): Qdrant collection override.
        filters (dict, optional): Metadata filters for retrieval.

    Response JSON: grounded generation result dict.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    query = payload.get("query")
    if not query or not isinstance(query, str) or not query.strip():
        return jsonify({"error": "query must be a non-empty string."}), 400

    top_k = payload.get("top_k", 10)
    if not isinstance(top_k, int) or top_k < 1:
        return jsonify({"error": "top_k must be a positive integer."}), 400

    try:
        from app.rag.tasks import run_generation_pipeline

        result = run_generation_pipeline(
            query=query,
            chunks=payload.get("chunks"),
            query_type=payload.get("query_type", ""),
            top_k=top_k,
            collection_name=payload.get("collection_name"),
            filters=payload.get("filters"),
        )
    except Exception as exc:
        logger.error("RAG generate failed: %s", exc)
        return jsonify({"error": f"Generation failed: {exc}"}), 500

    return jsonify(result)


@rag_bp.route("/query", methods=["POST"])
def query():
    """Full RAG pipeline: retrieve -> generate -> verify -> log (Phase 5).

    Request JSON:
        query (str, required): The user legal question.
        top_k (int, optional): Chunks to retrieve (default 10).
        filters (dict, optional): Metadata filters for retrieval.

    Response JSON: a ``RAGResponse``-schema dict including groundedness
    score, hallucination flag, and citation details.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    query_str = payload.get("query")
    if not query_str or not isinstance(query_str, str) or not query_str.strip():
        return jsonify({"error": "query must be a non-empty string."}), 400

    top_k = payload.get("top_k", 10)
    if not isinstance(top_k, int) or top_k < 1:
        return jsonify({"error": "top_k must be a positive integer."}), 400

    try:
        from app.rag.tasks import run_generation_pipeline
        result = run_generation_pipeline(
            query=query_str,
            top_k=top_k,
            collection_name=payload.get("collection_name"),
            filters=payload.get("filters"),
        )
    except Exception as exc:
        logger.error("RAG query failed: %s", exc)
        return jsonify({"error": f"RAG query failed: {exc}"}), 500

    return jsonify(result)


@rag_bp.route("/eval", methods=["POST"])
def eval_batch():
    """Batch evaluation endpoint (Phase 4).

    Request JSON:
        dataset (list, required): List of {"query", "expected_answer",
            "expected_citations"} dicts.
        eval_run_id (str, optional): UUID for the eval run.
        top_k (int, optional): Chunks per query (default 10).

    Response JSON: evaluation summary with per-query results and aggregate
    metric averages.
    """
    if not _rag_enabled():
        return jsonify({"error": "RAG is disabled."}), 503

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    dataset = payload.get("dataset")
    if not isinstance(dataset, list) or not dataset:
        return jsonify({"error": "dataset must be a non-empty list."}), 400

    top_k = payload.get("top_k", 10)
    if not isinstance(top_k, int) or top_k < 1:
        return jsonify({"error": "top_k must be a positive integer."}), 400

    try:
        from app.rag.tasks import run_evaluate
        result = run_evaluate(
            dataset=dataset,
            eval_run_id=payload.get("eval_run_id"),
            top_k=top_k,
        )
    except Exception as exc:
        logger.error("RAG eval failed: %s", exc)
        return jsonify({"error": f"Evaluation failed: {exc}"}), 500

    return jsonify(result)


# End of routes.py
