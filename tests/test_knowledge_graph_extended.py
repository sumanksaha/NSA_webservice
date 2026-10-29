"""Extended tests for the Knowledge Graph roadmap (Legal RAG support).

Covers:
- test_schema_alignment      every KGNode/KGEdge maps to knowledge_graph.json
- test_legal_edges           CITES / AMENDS / OVERRULES between sections
- test_adjudication_persistence  adjudication graphs stored in their own tables
- test_hybrid_search         POST /api/graph/search (mocked vector store)
- test_incremental_upsert    updated_at vs graph_last_modified change detection
"""

from __future__ import annotations

import pytest

from tests.test_knowledge_graph import (
    _dt,
    _make_adjudication,
    _make_case_file,
    _setup_test_env,
    _teardown_test_env,
)

from app.knowledge_graph.engine import (
    AMENDS,
    CITES,
    KGEdge,
    KGNode,
    KnowledgeGraphEngine,
    OVERRULES,
)
from app.knowledge_graph.errors import GraphBuildError
from app.knowledge_graph.schema_adapter import validate_graph


class TestSchemaAlignment:
    def test_schema_alignment(self):
        """Every node/edge produced by the engine validates against the canonical schema."""
        _app, _client, ctx = _setup_test_env()
        try:
            from app.extensions import db

            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            nodes = [
                KGNode(
                    id=n["data"]["id"],
                    label=n["data"]["label"],
                    type=n["data"]["type"],
                    source_table=n["data"].get("source_table"),
                    source_id=n["data"].get("source_id"),
                    metadata={
                        k: v
                        for k, v in n["data"].items()
                        if k not in {"id", "label", "type", "source_table", "source_id"}
                    },
                )
                for n in graph["nodes"]
            ]
            edges = [
                KGEdge(
                    source=e["data"]["source"],
                    target=e["data"]["target"],
                    type=e["data"]["type"],
                    weight=e["data"].get("weight", 1.0),
                )
                for e in graph["edges"]
            ]

            errors = validate_graph(nodes, edges)
            assert not errors, errors

            # Every engine node type maps to a canonical entity type.
            from app.knowledge_graph.schema_adapter import to_canonical

            canonical_types = {to_canonical(n.type) for n in nodes}
            assert canonical_types <= {
                "document",
                "authority",
                "evidence",
                "section",
                "reference",
            }
        finally:
            _teardown_test_env(ctx)


class TestLegalEdges:
    def test_legal_edges_between_sections(self):
        """CITES / AMENDS / OVERRULES edges use the canonical section:{id} target format."""
        _app, _client, ctx = _setup_test_env()
        try:
            from app.extensions import db

            case = _make_case_file(db)  # applicable_sections = "55,58,63"
            case.section_references = [  # type: ignore[attr-defined]
                {"id": "58", "type": "amends"},
                {"id": "63", "type": "overrules"},
                {"id": "99", "type": "cites"},  # no such section node -> skipped
            ]
            db.session.commit()

            case_node_id = f"case_file:{case.id}"
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            edges = [e["data"] for e in graph["edges"]]
            legal_edges = [e for e in edges if e["type"] in (AMENDS, OVERRULES, CITES)]

            amends = [e for e in legal_edges if e["type"] == AMENDS]
            overrules = [e for e in legal_edges if e["type"] == OVERRULES]
            cites = [e for e in legal_edges if e["type"] == CITES]

            assert len(amends) == 1
            assert amends[0]["source"] == case_node_id
            assert amends[0]["target"] == f"section:{case_node_id}:58"

            assert len(overrules) == 1
            assert overrules[0]["target"] == f"section:{case_node_id}:63"

            # Unknown reference id yields no edge.
            assert not any(e["target"].endswith(":99") for e in legal_edges)
            assert not cites

            # Case -> section edges still present.
            violated = [e for e in edges if e["type"] == "VIOLATED_SECTION"]
            assert {e["target"] for e in violated} == {
                f"section:{case_node_id}:55",
                f"section:{case_node_id}:58",
                f"section:{case_node_id}:63",
            }
        finally:
            _teardown_test_env(ctx)


class TestAdjudicationPersistence:
    def test_adjudication_persistence(self):
        """Adjudication graphs are stored in adjudication_* tables and retrievable."""
        _app, _client, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import AdjudicationEntity, AdjudicationRelationship, Entity

            adj = _make_adjudication(db)  # section_55=yes, section_56=yes
            graph = KnowledgeGraphEngine().build_graph_for_case(adj.id, "adjudication")
            assert graph["node_count"] > 0

            entities = AdjudicationEntity.query.filter_by(source_id=adj.id).all()
            assert len(entities) == 5  # case, fbo, inspector, 2 sections
            assert any(e.entity_type == "section" for e in entities)
            assert any(e.entity_type == "inspector" for e in entities)

            entity_ids = {e.id for e in entities}
            relationships = AdjudicationRelationship.query.filter(
                AdjudicationRelationship.source_id.in_(entity_ids) | AdjudicationRelationship.target_id.in_(entity_ids)
            ).all()
            assert len(relationships) > 0

            # Case-file table stays untouched.
            assert Entity.query.count() == 0

            # Re-build is idempotent on the adjudication tables (inspector node
            # shares source_id with the case, so it is replaced too).
            before = AdjudicationEntity.query.count()
            KnowledgeGraphEngine().build_graph_for_case(adj.id, "adjudication")
            after = AdjudicationEntity.query.count()
            assert after == before == 5
            assert after == before == 5
        finally:
            _teardown_test_env(ctx)


class TestHybridSearch:
    def _fake_store(self, results):
        class _Store:
            def search(self, query, k):  # noqa: ARG002
                return list(results)

        return _Store()

    def test_hybrid_search_returns_subgraph_with_scores(self):
        _app, _client, ctx = _setup_test_env()
        try:
            from app.extensions import db

            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")
            node_ids = [n["data"]["id"] for n in graph["nodes"]]
            section_ids = [nid for nid in node_ids if nid.startswith("section:")]
            assert section_ids, "expected section nodes"

            engine = KnowledgeGraphEngine()
            store = self._fake_store([(section_ids[0], 0.9), (node_ids[0], 0.5)])
            result = engine.hybrid_search("food safety standard", max_hops=1, k=5, vector_store=store)

            assert result["query"] == "food safety standard"
            assert result["seed_nodes"] == [section_ids[0], node_ids[0]]
            assert result["scores"][section_ids[0]] == 0.9
            assert result["scores"][node_ids[0]] == 0.5

            result_ids = {n["id"] for n in result["nodes"]}
            assert section_ids[0] in result_ids
            assert node_ids[0] in result_ids

            edge_types = {e["type"] for e in result["edges"]}
            assert "VIOLATED_SECTION" in edge_types
            assert all(e["source"] in result_ids and e["target"] in result_ids for e in result["edges"])
        finally:
            _teardown_test_env(ctx)

    def test_hybrid_search_endpoint(self, monkeypatch):
        """POST /api/graph/search: 200 + subgraph; 400 structured payloads on errors."""
        _app, client, ctx = _setup_test_env()
        try:
            from app.extensions import db

            case = _make_case_file(db)
            KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            payload = {
                "query": "milk standard",
                "seed_nodes": ["section:case_file:1:55"],
                "nodes": [
                    {
                        "id": "section:case_file:1:55",
                        "label": "Section 55",
                        "type": "section",
                        "source_table": "kg_sections",
                        "source_id": None,
                        "metadata": {},
                        "score": 0.9,
                    }
                ],
                "edges": [],
                "scores": {"section:case_file:1:55": 0.9},
            }

            class _FakeEngine:
                def hybrid_search(self, query, max_hops=2, k=5, vector_store=None):  # noqa: ARG002
                    return dict(payload, query=query)

            monkeypatch.setattr("app.knowledge_graph.api.KnowledgeGraphEngine", _FakeEngine)

            resp = client.post(
                "/api/graph/search",
                json={"query": "milk standard", "max_hops": 1, "k": 5},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["query"] == "milk standard"
            assert data["nodes"][0]["id"] == "section:case_file:1:55"

            # Missing query -> 400 structured payload.
            resp = client.post("/api/graph/search", json={})
            assert resp.status_code == 400
            assert resp.get_json()["code"] == "invalid_request"

            # Engine failure -> 400 structured payload.
            class _ErrorEngine:
                def hybrid_search(self, *args, **kwargs):  # noqa: ARG002
                    raise GraphBuildError("index unavailable")

            monkeypatch.setattr("app.knowledge_graph.api.KnowledgeGraphEngine", _ErrorEngine)
            resp = client.post("/api/graph/search", json={"query": "x"})
            assert resp.status_code == 400
            assert resp.get_json()["code"] == "graph_build_error"
        finally:
            _teardown_test_env(ctx)


class TestIncrementalUpsert:
    def test_incremental_upsert(self):
        _app, _client, ctx = _setup_test_env()
        try:
            from app.extensions import db
            from app.models import Entity, Relationship

            case = _make_case_file(db)  # applicable_sections = "55,58,63"
            case.updated_at = _dt(1, 1, 2026)
            db.session.commit()

            engine = KnowledgeGraphEngine()

            # First upsert writes the graph.
            result = engine.upsert_graph(case.id, "case_file")
            assert result["status"] == "upserted"
            assert result["nodes"] > 0
            assert any(
                e.entity_type == "section"
                for e in Entity.query.filter_by(source_table="case_files", source_id=case.id).all()
            )

            # No changes since -> no-op.
            result = engine.upsert_graph(case.id, "case_file")
            assert result["status"] == "unchanged"

            # Remove a section + bump updated_at -> graph is rebuilt.
            case.applicable_sections = "55,63"
            case.updated_at = _dt(2, 1, 2026)
            db.session.commit()

            result = engine.upsert_graph(case.id, "case_file")
            assert result["status"] == "upserted"
            sections = {
                e.name
                for e in Entity.query.filter_by(
                    entity_type="section", source_table="case_files", source_id=case.id
                ).all()
            }
            assert sections == {"Section 55", "Section 63"}

            # Stale edges removed: every relationship references a current entity.
            current_entity_ids = {
                e.id for e in Entity.query.filter_by(source_table="case_files", source_id=case.id).all()
            }
            for rel in Relationship.query.all():
                assert rel.source_id in current_entity_ids
                assert rel.target_id in current_entity_ids

            # graph_last_modified stamped on persisted rows.
            assert all(
                e.graph_last_modified is not None
                for e in Entity.query.filter_by(source_table="case_files", source_id=case.id).all()
            )
        finally:
            _teardown_test_env(ctx)

    def test_upsert_unknown_case_raises(self):
        _app, _client, ctx = _setup_test_env()
        try:
            engine = KnowledgeGraphEngine()
            with pytest.raises(GraphBuildError):
                engine.upsert_graph(99999, "case_file")
        finally:
            _teardown_test_env(ctx)
