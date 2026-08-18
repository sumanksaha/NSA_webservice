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
from unittest.mock import MagicMock, patch

import pytest

# Ensure .env is loaded for tests that hit the real DB
from dotenv import load_dotenv

load_dotenv()

NEO4J_AVAILABLE = bool(os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_PASSWORD"))

#: Fail-closed write guard (2026-08-12): ``push_to_neo4j`` clears the WHOLE
#: graph (``MATCH (n) DETACH DELETE n``) before pushing.  The real-connection
#: tests below are DESTRUCTIVE — they wiped the 29k-node legal KG from a live
#: Aura instance in an earlier run.  They now require an explicit
#: ``NEO4J_ALLOW_WRITE=1`` on top of credentials.
NEO4J_WRITES_ALLOWED = os.environ.get("NEO4J_ALLOW_WRITE", "0").lower() in ("1", "true", "yes")

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


@pytest.mark.skipif(
    not NEO4J_AVAILABLE or not NEO4J_WRITES_ALLOWED,
    reason="Neo4j credentials missing, or NEO4J_ALLOW_WRITE not set "
    "(push_to_neo4j CLEARS the whole graph with MATCH (n) DETACH DELETE n)",
)
class TestNeo4jRealConnection:
    """End-to-end tests against the real Aura instance.

    DESTRUCTIVE: ``push_to_neo4j`` clears the whole graph before pushing.
    Runs only when ``NEO4J_ALLOW_WRITE=1`` is set explicitly (default off).
    """

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
# Write-guard tests (no DB contact needed)
# --------------------------------------------------------------------------- #


class TestNeo4jWriteGuard:
    """Fail-closed NEO4J_ALLOW_WRITE guard on destructive write paths."""

    @staticmethod
    def _env_with_creds_without_flag() -> dict:
        env = dict(os.environ)
        env["NEO4J_URI"] = "neo4j+s://test.databases.neo4j.io"
        env["NEO4J_USERNAME"] = "neo4j"
        env["NEO4J_PASSWORD"] = "secret"
        env.pop("NEO4J_ALLOW_WRITE", None)
        return env

    def test_push_to_neo4j_blocked_without_write_flag(self):
        """push_to_neo4j must refuse to run (and not touch the DB) without the flag."""
        from app.services.neo4j_graph import push_to_neo4j

        with patch.dict(os.environ, self._env_with_creds_without_flag(), clear=True):
            with pytest.raises(RuntimeError, match="NEO4J_ALLOW_WRITE"):
                push_to_neo4j()

    def test_clear_legal_kg_blocked_without_write_flag(self):
        """clear_legal_kg must refuse to delete the legal KG without the flag."""
        from kg.schema import clear_legal_kg

        with patch.dict(os.environ, self._env_with_creds_without_flag(), clear=True):
            with pytest.raises(RuntimeError, match="NEO4J_ALLOW_WRITE"):
                clear_legal_kg()

    def test_writes_allowed_flag_semantics(self):
        """neo4j_writes_allowed() is fail-closed: off by default, on with =1."""
        from app.services.neo4j_graph import neo4j_writes_allowed

        with patch.dict(os.environ, {}, clear=True):
            assert neo4j_writes_allowed() is False
        for value in ("1", "true", "YES"):
            with patch.dict(os.environ, {"NEO4J_ALLOW_WRITE": value}, clear=True):
                assert neo4j_writes_allowed() is True

    def test_push_clear_is_scoped_to_case_graph(self):
        """push_to_neo4j must clear ONLY case-file labels — never the legal KG.

        Runs the full push path against a recording fake driver with an
        empty payload; asserts the pre-push clear is the scoped Cypher and
        is NOT the bare whole-graph wipe.
        """
        from app.services.neo4j_graph import push_to_neo4j

        class RecordingDriver:
            def __init__(self):
                self.calls: list[str] = []

            def execute_query(self, cypher, parameters_=None, database_=None):
                self.calls.append(cypher)
                return object()

            def close(self):
                pass

        driver = RecordingDriver()
        with patch.dict(os.environ, {"NEO4J_ALLOW_WRITE": "1"}, clear=False):
            with patch(
                "app.services.neo4j_graph.build_cypher_payload",
                return_value={"nodes": [], "edges": [], "node_count": 0, "edge_count": 0},
            ):
                with patch("app.services.neo4j_graph.setup_constraints_and_indexes"):
                    with patch("app.services.neo4j_graph._get_driver", return_value=driver):
                        result = push_to_neo4j()

        from app.services.neo4j_graph import _CASE_GRAPH_LABELS

        assert result["deleted"] == "all"
        clear_calls = [c for c in driver.calls if "DETACH DELETE" in c]
        assert clear_calls, "push_to_neo4j should issue a pre-push clear"
        # The legal-KG destroying bare wipe must NEVER be issued — check EVERY
        # clear call, not just the first.
        assert all("MATCH (n) DETACH DELETE n" not in c for c in clear_calls)
        clear_cypher = clear_calls[0]
        # The clear is scoped to the case-file labels (derived from the same
        # source as the push) + the local_id / provision_id guards.
        assert "n.local_id IS NOT NULL" in clear_cypher
        assert "n.provision_id IS NULL" in clear_cypher
        assert set(_CASE_GRAPH_LABELS) <= {"Case", "FBO", "Inspector", "Sample", "Lab", "Section", "Evidence", "Ancillary", "Entity"}
        for label in _CASE_GRAPH_LABELS:
            assert f"n:{label}" in clear_cypher
        for legal_label in ("Act", "LegalProvision", "LegalDomain", "LegalConcept", "Chunk", "Document"):
            assert f"n:{legal_label}" not in clear_cypher

    def test_sync_task_returns_clean_error_when_writes_blocked(self):
        """The sync task surfaces the guard as a clean error status, not a 500."""
        from app.knowledge_graph.tasks import _run_sync_kg_to_neo4j

        with patch("app.services.neo4j_graph.neo4j_configured", return_value=True):
            with patch(
                "app.services.neo4j_graph.push_to_neo4j",
                side_effect=RuntimeError("set NEO4J_ALLOW_WRITE=1"),
            ):
                result = _run_sync_kg_to_neo4j()
                assert result["status"] == "error"
                assert "NEO4J_ALLOW_WRITE" in result["message"]


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
        assert callable(sync_kg_to_neo4j)


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


class TestSyncNeo4jRoute:
    """Test the /api/sync-neo4j route."""

    def test_route_returns_503_when_not_configured(self):
        from flask import Flask

        from app.knowledge_graph import kg_bp

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
        from flask import Flask

        from app.knowledge_graph import kg_bp

        app = Flask(__name__)
        app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")

        with app.test_client() as client:
            with patch("app.services.neo4j_graph.neo4j_configured", return_value=True):
                with patch("app.utils.qstash_client.qstash_configured", return_value=False):
                    with patch("app.utils.qstash_client.resolve_task", return_value=MagicMock()):
                        # The sync fallback calls resolve_task().apply()
                        MagicMock()

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
        from flask import Flask

        from app.knowledge_graph import kg_bp

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
        # Use app context with mock entities
        from app import create_app
        from app.services.neo4j_graph import build_cypher_payload

        app = create_app()
        with app.app_context():
            payload = build_cypher_payload()
            assert "nodes" in payload
            assert "edges" in payload
            assert "node_count" in payload
            assert "edge_count" in payload
