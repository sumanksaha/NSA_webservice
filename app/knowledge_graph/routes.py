"""Knowledge-graph routes (plan.md Phase 14).

Serves the Cytoscape.js visualizer page and the JSON API that supplies
the graph data.  Case IDs are disambiguated via :class:`CaseResolver`.
"""

from __future__ import annotations

from flask import abort, jsonify, render_template, request

from app.knowledge_graph import kg_bp
from app.knowledge_graph.engine import KnowledgeGraphEngine

engine = KnowledgeGraphEngine()


def _resolve(case_id: int):
    """Resolve the path ID to a case, honouring the optional ``?kind=`` param."""
    from app.shared.case_resolver import CaseResolver

    return CaseResolver().resolve(
        case_id,
        kind=request.args.get("kind"),
    )


@kg_bp.route("/case/<int:case_id>")
def view(case_id: int):
    """Render the interactive Cytoscape.js knowledge-graph page for a case."""
    resolved = _resolve(case_id)
    if resolved is None:
        abort(404)

    return render_template(
        "knowledge_graph/view.html",
        case_number=resolved.case_number,
        case_id=resolved.case_id,
        adjudication_id=resolved.adjudication_id,
        case_type=resolved.case_type,
        api_url=f"/knowledge-graph/api/case/{case_id}",
    )


@kg_bp.route("/api/case/<int:case_id>")
def api(case_id: int):
    """Return the knowledge-graph JSON payload (Cytoscape.js elements).

    For ``case_file`` cases the nodes and edges are (re)persisted to the
    ``entity`` / ``relationship`` tables on every call, keeping the DB
    store in sync with the record state.
    """
    resolved = _resolve(case_id)
    if resolved is None:
        return jsonify({"error": "Case not found"}), 404

    payload = engine.build_graph_for_case(
        case_id,
        case_type=resolved.case_type,
    )
    if "error" in payload:
        return jsonify(payload), 404
    return jsonify(payload)


@kg_bp.route("/api/sync-neo4j", methods=["POST"])
@kg_bp.route("/api/sync-neo4j/<int:case_id>", methods=["POST"])
def sync_neo4j(case_id: int | None = None):
    """Push the knowledge graph to Neo4j Aura asynchronously via QStash.

    Without QStash configured, falls back to synchronous execution.

    Args:
        case_id: Optional case ID - if omitted, syncs the entire graph.
    """
    from app.services.neo4j_graph import neo4j_configured
    from app.utils.qstash_client import publish_task

    if not neo4j_configured():
        return jsonify({"error": "Neo4j not configured in .env"}), 503

    payload: dict = {}
    if case_id is not None:
        payload["case_id"] = case_id

    result = publish_task(
        "sync_kg_to_neo4j",
        payload,
        dedup_key=f"sync_kg-{case_id or 'all'}",
    )

    if result["mode"] == "async":
        return jsonify({
            "status": "queued",
            "message": "Knowledge graph sync scheduled via QStash",
            "message_id": result["message_id"],
            "task": "sync_kg_to_neo4j",
        })
    else:
        return jsonify({
            "status": "complete",
            "message": "Knowledge graph synced to Neo4j",
            "result": result.get("result", {}),
        })
