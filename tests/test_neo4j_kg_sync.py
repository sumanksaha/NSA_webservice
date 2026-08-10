"""Tests for the Neo4j Aura knowledge-graph sync integration.

Covers:
- neo4j_configured() env-var detection
- push_to_neo4j() end-to-end (real Aura connection via .env credentials)
- query_neo4j() query execution
- sync_kg_to_neo4j task (Celery + sync fallback)
- /api/sync-neo4j route (async QStash + sync fallback)
"""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest

# Ensure .env is loaded for tests that hit the real DB
from dotenv import load_dotenv

load_dotenv()

NEO4J_AVAILABLE = bool(os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_PASSWORD"))
neo4j = pytest.importorskip("neo4j", reason="neo4j driver not installed")


# --------------------------------------------------------------------------- #
# Config detection
# --------------------------------------------------------------------------- #


class TestNeo4jConfig:
    """Test neo4j_configured() behaviour."""

    def test_configured_with_all_env_vars(self):
        from app.services.neo4j_graph import neo4j_configured

        with patch.dict(
            os.environ,
            {
                "NEO4J_URI": "neo4j+s://test.databases.neo4j.io",
                "NEO4J_USERNAME": "neo4j",
                "NEO4J_PASSWORD": "secret",
            },
            clear=False,
        ):
            assert neo4j_configured() is True

    def test_not_configured_missing_uri(self):
        from app.services.neo4j_graph import neo4j_configured

        env = dict(os.environ)
        env.pop("NEO4J_URI", None)
        with patch.dict(os.environ, env, clear=True):
            assert neo4j_configured() is False

    def test_not_configured_missing_password(self):
        from app.services.neo4j_graph import neo4j_configured

        env = dict(os.environ)
        env.pop("NEO4J_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            assert neo4j_configured() is False


# --------------------------------------------------------------------------- #
# Real connection tests (skip if no credentials)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not NEO4J_AVAILABLE, reason="Neo4j credentials not in .env")
class TestNeo4jRealConnection:
    """End-to-end tests against the real Aura instance."""

    def test_push_empty_graph(self):
        """Push with no entities — should succeed with 0 nodes/edges."""
        from app.services.neo4j_graph import push_to_neo4j

        # Mock build_cypher_payload to return empty data without needing a DB
        with patch(
            "app.services.neo4j_graph.build_cypher_payload",
            return_value={"nodes": [], "edges": [], "node_count": 0, "edge_count": 0},
        ):
            result = push_to_neo4j()
            assert result["nodes"] == 0
            assert result["edges"] == 0
            assert result["deleted"] == "all"

    def test_query_neo4j_returns_list(self):
        from app.services.neo4j_graph import query_neo4j

        # Count nodes — should work on a fresh or existing database
        result = query_neo4j("MATCH (n) RETURN count(n) AS total")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_push_sample_data(self):
        """Push a small sample graph and verify it persists."""
        from app.services.neo4j_graph import push_to_neo4j, query_neo4j

        # Mock build_cypher_payload with a small graph
        sample = {
            "nodes": [
                {
                    "id": 1,
                    "label": "Case",
                    "name": "Test Case",
                    "entity_type": "case",
                    "source_table": None,
                    "source_id": None,
                },
                {
                    "id": 2,
                    "label": "Person",
                    "name": "Test Person",
                    "entity_type": "person",
                    "source_table": None,
                    "source_id": None,
                },
            ],
            "edges": [
                {"source_id": 1, "target_id": 2, "type": "VIOLATES", "weight": 1.0},
            ],
            "node_count": 2,
            "edge_count": 1,
        }

        with patch("app.services.neo4j_graph.build_cypher_payload", return_value=sample):
            result = push_to_neo4j()
            assert result["nodes"] == 2
            assert result["edges"] == 1

        # Verify nodes exist in Neo4j
        nodes = query_neo4j("MATCH (n) RETURN count(n) AS total")
        assert nodes[0]["total"] >= 2

    def test_setup_constraints_and_indexes(self):
        from app.services.neo4j_graph import setup_constraints_and_indexes

        result = setup_constraints_and_indexes()
        assert "constraints_added" in result
        assert "indexes_added" in result
        assert "existing" in result

    def test_apoc_used_flag(self):
        """Verify push_to_neo4j reports apoc_used correctly."""
        from app.services.neo4j_graph import push_to_neo4j

        with patch(
            "app.services.neo4j_graph.build_cypher_payload",
            return_value={"nodes": [], "edges": [], "node_count": 0, "edge_count": 0},
        ):
            result = push_to_neo4j(use_apoc=True)
            assert result["apoc_used"] is True


# --------------------------------------------------------------------------- #
# Task tests (no real DB needed)
# --------------------------------------------------------------------------- #


class TestSyncKgTask:
    """Test the Celery task and sync fallback."""

    def test_task_not_configured(self):
        from app.knowledge_graph.tasks import _run_sync_kg_to_neo4j  # pyright: ignore[reportMissingImports]

        with patch.dict(os.environ, {}, clear=True):
            result = _run_sync_kg_to_neo4j()
            assert result["status"] == "error"
            assert "not configured" in result["message"]

    def test_task_with_mock_push(self):
        from app.knowledge_graph.tasks import _run_sync_kg_to_neo4j  # pyright: ignore[reportMissingImports]

        with patch("app.services.neo4j_graph.neo4j_configured", return_value=True):
            with patch(
                "app.services.neo4j_graph.push_to_neo4j",
                return_value={"nodes": 5, "edges": 3, "deleted": "all", "created": "new"},
            ):
                result = _run_sync_kg_to_neo4j()
                assert result["status"] == "ok"
                assert result["nodes"] == 5
                assert result["edges"] == 3

    def test_task_exposed_as_callable(self):
        """The task should be callable whether or not Celery is available."""
        from app.knowledge_graph.tasks import sync_kg_to_neo4j  # pyright: ignore[reportMissingImports]

        # Should have either __call__ (fallback) or run (Celery task)
        assert hasattr(sync_kg_to_neo4j, "__call__")


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


class TestSyncNeo4jRoute:
    """Test the /api/sync-neo4j route."""

    def test_route_returns_503_when_not_configured(self):
        from app.knowledge_graph import kg_bp
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")

        with app.test_client() as client:
            with patch("app.services.neo4j_graph.neo4j_configured", return_value=False):
                resp = client.post("/knowledge-graph/api/sync-neo4j")
                assert resp.status_code == 503
                data = resp.get_json()
                assert data["error"] == "Neo4j not configured in .env"

    def test_route_sync_fallback(self):
        """When QStash is not configured, should fall back to sync."""
        from app.knowledge_graph import kg_bp
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")

        with app.test_client() as client:
            with patch("app.services.neo4j_graph.neo4j_configured", return_value=True):
                with patch("app.utils.qstash_client.qstash_configured", return_value=False):
                    with patch("app.utils.qstash_client.resolve_task", return_value=MagicMock()):
                        # The sync fallback calls resolve_task().apply()
                        mock_result = MagicMock()

                        def mock_apply(**kwargs):
                            result = MagicMock()
                            result.result = {"status": "ok", "nodes": 3, "edges": 2, "deleted": "all", "created": "new"}
                            return result

                        mock_task = MagicMock()
                        mock_task.apply = mock_apply

                        with patch("app.utils.qstash_client.resolve_task", return_value=mock_task):
                            resp = client.post("/knowledge-graph/api/sync-neo4j")
                            assert resp.status_code == 200
                            data = resp.get_json()
                            assert data["status"] == "complete"
                            assert data["result"]["nodes"] == 3

    def test_route_async_via_qstash(self):
        """When QStash is configured, should return queued status."""
        from app.knowledge_graph import kg_bp
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")

        with app.test_client() as client:
            with patch("app.services.neo4j_graph.neo4j_configured", return_value=True):
                with patch("app.utils.qstash_client.qstash_configured", return_value=True):
                    with patch("app.utils.qstash_client.publish_task") as mock_publish:
                        mock_publish.return_value = {
                            "mode": "async",
                            "message_id": "msg-123",
                            "deduplicated": False,
                        }
                        resp = client.post("/knowledge-graph/api/sync-neo4j/42")
                        assert resp.status_code == 200
                        data = resp.get_json()
                        assert data["status"] == "queued"
                        assert data["message_id"] == "msg-123"
                        mock_publish.assert_called_once()


# --------------------------------------------------------------------------- #
# Build payload tests
# --------------------------------------------------------------------------- #


class TestBuildCypherPayload:
    """Test the Cypher payload builder."""

    def test_returns_dict_with_expected_keys(self):
        from app.services.neo4j_graph import build_cypher_payload

        # Use app context with mock entities
        from app import create_app

        app = create_app()
        with app.app_context():
            payload = build_cypher_payload()
            assert "nodes" in payload
            assert "edges" in payload
            assert "node_count" in payload
            assert "edge_count" in payload
