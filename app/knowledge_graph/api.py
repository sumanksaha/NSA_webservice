"""Hybrid graph search API.

Endpoint: ``POST /api/graph/search`` with payload
``{"query": "...", "max_hops": 2, "k": 5}``.

Returns a sub-graph JSON (nodes + edges) plus per-node similarity scores.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.knowledge_graph.engine import KnowledgeGraphEngine
from app.knowledge_graph.errors import GraphBuildError

graph_search_bp = Blueprint("graph_search", __name__, url_prefix="/api/graph")


@graph_search_bp.route("/search", methods=["POST"])
def search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    try:
        max_hops = int(data.get("max_hops", 2))
        k = int(data.get("k", 5))
    except (TypeError, ValueError) as exc:
        return jsonify({"error": "max_hops and k must be integers", "code": "invalid_request"}), 400

    if not query:
        return jsonify({"error": "query is required", "code": "invalid_request"}), 400

    try:
        engine = KnowledgeGraphEngine()
        result = engine.hybrid_search(query=query, max_hops=max_hops, k=k)
        return jsonify(result), 200
    except GraphBuildError as exc:
        return jsonify({"error": str(exc), "code": "graph_build_error"}), 400
