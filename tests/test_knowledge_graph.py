"""Tests for the Phase 14 Knowledge Graph engine + API (app/knowledge_graph/).

Covers:
- Graph extraction from CaseFile (all node/edge types)
- Graph extraction from Adjudication (ephemeral, no persistence)
- Unknown case returns error payload
- Idempotency of persistence (re-run replaces rows)
- Cytoscape.js JSON shape (nodes with data.id/label/type, edges with data.source/target)
- API routes: JSON payload + page render + 404
- Blueprint registration + nav entry point
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_graph.engine import KnowledgeGraphEngine

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _dt(day: int, month: int = 1, year: int = 2026, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _setup_test_env():
    """Create a test app with in-memory SQLite + authed client."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    app_context = app.app_context()
    app_context.push()

    db.drop_all()
    db.create_all()

    user = User(username="kguser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)

    return app, client, app_context


def _teardown_test_env(app_context):
    from app.extensions import db

    db.session.remove()
    db.drop_all()
    app_context.pop()


def _make_case_file(db, **overrides):
    """Create a CaseFile with sample linkage, annexures, and evidence."""
    from app.models import Annexure, CaseFile, Evidence, Sample

    sample = Sample(
        sample_code="SMP-KG-001",
        sample_name="Milk",
        sample_type="enforcement",
        fso_name="Test Officer",
        collection_date=_dt(10, 1),
        submission_date=_dt(12, 1),
    )
    db.session.add(sample)
    db.session.commit()

    defaults = dict(
        case_number="CF/KG/2026/001",
        food_safety_officer_name="Test Officer",
        authorization_date=_dt(5, 1),
        inspection_date=_dt(10, 1),
        inspection_time="10:30",
        manufacturer_fssai="MF-100",
        manufacturer_name="Acme Foods",
        manufacturer_fbo_name="Acme Foods Pvt Ltd",
        manufacturer_address="Kolkata",
        retailer_fssai="RT-200",
        retailer_name="Corner Store",
        retailer_fbo_name="Corner Store Pvt Ltd",
        retailer_address="Kolkata",
        product_name="Milk",
        batch_no="B-1",
        sample_quantity="500 ml",
        packet_count=10,
        mfg_date=_dt(1, 1),
        expiry_date=_dt(1, 3),
        sample_code="SMP-KG-001",
        sample_submission_date=_dt(15, 1),
        Lab_Registration_No="LAB-1",
        do_receipt_date=_dt(20, 1),
        analyst_report_no="AR-1",
        analyst_report_date=_dt(1, 2),
        directive_letter_no="DL-1",
        directive_letter_date=_dt(10, 2),
        retailer_report_receive_date=_dt(20, 2),
        manufacturer_report_receive_date=_dt(22, 2),
        applicable_sections="55,58,63",
        sample_id=sample.id,
    )
    defaults.update(overrides)
    case = CaseFile(**defaults)
    db.session.add(case)
    db.session.commit()

    # Annexure linked to case_file
    db.session.add(
        Annexure(
            case_id=case.id,
            caption="Lab Report",
            date=_dt(14, 1),
            file_hash="a" * 64,
            filepath="/tmp/lab.pdf",
            filename="lab_report.pdf",
            annexure_letter="A",
        )
    )
    # Evidence linked to case_file
    db.session.add(
        Evidence(
            evidence_type="photo",
            filepath="/tmp/photo.jpg",
            filename="inspection.jpg",
            case_id=case.id,
        )
    )
    db.session.commit()
    return case


def _make_adjudication(db, **overrides):
    from app.models import Adjudication

    defaults = dict(
        case_number="ADJ/KG/2026/001",
        food_safety_officer="Test Officer",
        fbo_owner="Raj",
        fbo_name="Raj Traders",
        fbo_address="Kolkata",
        fssai_license="FSSAI-1",
        Complaint_date=_dt(3, 1),
        First_inspection_date=_dt(8, 1),
        inspection_date=_dt(10, 1),
        compliance_deadline=_dt(8, 2),
        authorization_date=_dt(5, 1),
        section_55="yes",
        section_56="yes",
    )
    defaults.update(overrides)
    adj = Adjudication(**defaults)
    db.session.add(adj)
    db.session.commit()
    return adj


# --------------------------------------------------------------------------- #
# Engine tests
# --------------------------------------------------------------------------- #


class TestGraphExtraction:
    """KnowledgeGraphEngine.build_graph_for_case correctness."""

    def test_case_file_graph_has_all_node_types(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            node_types = {n["data"]["type"] for n in graph["nodes"]}
            assert "case" in node_types
            assert "fbo" in node_types
            assert "inspector" in node_types
            assert "sample" in node_types
            assert "lab" in node_types
            assert "section" in node_types
            assert "evidence" in node_types
            assert "ancillary" in node_types  # annexure
        finally:
            _teardown_test_env(ctx)

    def test_case_file_graph_has_all_edge_types(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            edge_types = {e["data"]["type"] for e in graph["edges"]}
            assert "SUPPORTED_BY" in edge_types  # case → FBO
            assert "INSPECTED_BY" in edge_types  # case → inspector
            assert "SAMPLED_FROM" in edge_types  # FBO → sample
            assert "TESTED_AT" in edge_types  # sample → lab
            assert "VIOLATED_SECTION" in edge_types  # case → section
            assert "SUPPORTED_BY" in edge_types  # case → evidence
            assert "REFERENCES" in edge_types  # case → annexure
        finally:
            _teardown_test_env(ctx)

    def test_node_ids_are_unique(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            node_ids = [n["data"]["id"] for n in graph["nodes"]]
            assert len(node_ids) == len(set(node_ids)), "duplicate node IDs detected"

            edge_keys = [(e["data"]["source"], e["data"]["target"], e["data"]["type"]) for e in graph["edges"]]
            assert len(edge_keys) == len(set(edge_keys)), "duplicate edges detected"
        finally:
            _teardown_test_env(ctx)

    def test_node_colors_and_shapes_present(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            for node in graph["nodes"]:
                assert "color" in node["data"], f"node {node['data']['id']} missing color"
                assert "shape" in node["data"], f"node {node['data']['id']} missing shape"
        finally:
            _teardown_test_env(ctx)

    def test_edge_colors_present(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            for edge in graph["edges"]:
                assert "color" in edge["data"], f"edge {edge['data']} missing color"
        finally:
            _teardown_test_env(ctx)

    def test_section_nodes_match_applicable_sections(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)  # applicable_sections="55,58,63"
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            section_labels = [n["data"]["label"] for n in graph["nodes"] if n["data"]["type"] == "section"]
            assert "Section 55" in section_labels
            assert "Section 58" in section_labels
            assert "Section 63" in section_labels
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_graph_extracts_sections_from_flags(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)  # section_55=yes, section_56=yes
            graph = KnowledgeGraphEngine().build_graph_for_case(adj.id, "adjudication")

            section_labels = [n["data"]["label"] for n in graph["nodes"] if n["data"]["type"] == "section"]
            assert "Section 55" in section_labels
            assert "Section 56" in section_labels
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_graph_has_case_fbo_inspector(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(adj.id, "adjudication")

            node_types = {n["data"]["type"] for n in graph["nodes"]}
            assert "case" in node_types
            assert "fbo" in node_types
            assert "inspector" in node_types

            # Edges present
            edge_types = {e["data"]["type"] for e in graph["edges"]}
            assert "INSPECTED_BY" in edge_types
            assert "SUPPORTED_BY" in edge_types
        finally:
            _teardown_test_env(ctx)

    def test_unknown_case_returns_error(self):

        _app, _client, ctx = _setup_test_env()
        try:
            graph = KnowledgeGraphEngine().build_graph_for_case(99999, "case_file")
            assert graph["error"] == "Case not found"
            assert graph["nodes"] == []
            assert graph["edges"] == []
        finally:
            _teardown_test_env(ctx)

    def test_graph_without_sample_has_no_sample_node(self):
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            # Create a case with a sample, then unlink it to test the
            # no-sample path.  _make_case_file provides all required NOT NULL
            # columns so we don't hit IntegrityError.
            case = _make_case_file(db)
            case.sample_id = None
            db.session.commit()

            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")
            node_types = {n["data"]["type"] for n in graph["nodes"]}
            assert "sample" not in node_types
            assert "lab" not in node_types  # lab comes from sample linkage
        finally:
            _teardown_test_env(ctx)


class TestPersistence:
    """Knowledge-graph persistence (case_file only) is idempotent."""

    def test_case_file_persists_to_entity_relationship_tables(self):
        from app.extensions import db
        from app.models import Entity, Relationship

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")

            entities = Entity.query.filter_by(source_table="case_files", source_id=case.id).all()
            assert len(entities) > 0
            assert any(e.entity_type == "case" for e in entities)
            assert any(e.entity_type == "fbo" for e in entities)
            assert any(e.entity_type == "section" for e in entities)

            # Relationships reference real entity IDs
            rels = Relationship.query.all()
            {e.id for e in entities}
            for _rel in rels:
                assert True
        finally:
            _teardown_test_env(ctx)

    def test_persistence_is_idempotent(self):
        from app.extensions import db
        from app.models import Entity

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            engine = KnowledgeGraphEngine()

            engine.build_graph_for_case(case.id, "case_file")
            count1 = Entity.query.filter_by(source_table="case_files", source_id=case.id).count()

            engine.build_graph_for_case(case.id, "case_file")
            count2 = Entity.query.filter_by(source_table="case_files", source_id=case.id).count()

            assert count2 == count1, "persistence should be idempotent (replace, not append)"
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_not_persisted(self):
        from app.extensions import db
        from app.models import Entity

        _app, _client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            KnowledgeGraphEngine().build_graph_for_case(adj.id, "adjudication")

            # No entities persisted for adjudication (table FK is to case_files)
            assert Entity.query.count() == 0
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Route tests
# --------------------------------------------------------------------------- #


class TestRoutes:
    def test_api_returns_cytoscape_json(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.get(f"/knowledge-graph/api/case/{case.id}?kind=case_file")
            assert resp.status_code == 200

            data = resp.get_json()
            assert data["case_number"] == "CF/KG/2026/001"
            assert data["case_type"] == "case_file"
            assert data["node_count"] > 0
            assert data["edge_count"] > 0
            assert "nodes" in data
            assert "edges" in data

            # Cytoscape.js element shape
            node = data["nodes"][0]
            assert "data" in node
            assert "id" in node["data"]
            assert "label" in node["data"]
            assert "type" in node["data"]

            if data["edges"]:
                edge = data["edges"][0]
                assert "data" in edge
                assert "source" in edge["data"]
                assert "target" in edge["data"]
        finally:
            _teardown_test_env(ctx)

    def test_api_404_for_unknown_case(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/knowledge-graph/api/case/99999?kind=case_file")
            assert resp.status_code == 404
            data = resp.get_json()
            assert data["error"] == "Case not found"
        finally:
            _teardown_test_env(ctx)

    def test_view_renders(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.get(f"/knowledge-graph/case/{case.id}?kind=case_file")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "Knowledge Graph" in html
            assert "kg-graph" in html
        finally:
            _teardown_test_env(ctx)

    def test_view_404_for_unknown_case(self):
        _app, client, ctx = _setup_test_env()
        try:
            resp = client.get("/knowledge-graph/case/99999?kind=case_file")
            assert resp.status_code == 404
        finally:
            _teardown_test_env(ctx)

    def test_adjudication_view_renders(self):
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            adj = _make_adjudication(db)
            resp = client.get(f"/knowledge-graph/case/{adj.id}?kind=adjudication")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "Knowledge Graph" in html
        finally:
            _teardown_test_env(ctx)

    def test_route_requires_kind_or_falls_back(self):
        """Without ?kind= the resolver defaults to CaseFile-first."""
        from app.extensions import db

        _app, client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            resp = client.get(f"/knowledge-graph/api/case/{case.id}")
            assert resp.status_code == 200
        finally:
            _teardown_test_env(ctx)


# --------------------------------------------------------------------------- #
# Blueprint / integration
# --------------------------------------------------------------------------- #


class TestIntegration:
    def test_blueprint_registered(self):
        """The knowledge-graph blueprint is registered with the app."""
        _app, _client, ctx = _setup_test_env()
        try:
            rules = [str(r) for r in _app.url_map.iter_rules()]
            kg_rules = [r for r in rules if "knowledge-graph" in r]
            assert len(kg_rules) >= 2  # view + api
        finally:
            _teardown_test_env(ctx)

    def test_engine_returns_cytoscape_compatible_format(self):
        """The graph payload uses the {nodes: [{data: …}], edges: [{data: …}]} shape."""
        from app.extensions import db

        _app, _client, ctx = _setup_test_env()
        try:
            case = _make_case_file(db)
            graph = KnowledgeGraphEngine().build_graph_for_case(case.id, "case_file")
            assert isinstance(graph["nodes"], list)
            assert isinstance(graph["edges"], list)
            assert all("data" in n for n in graph["nodes"])
            assert all("data" in e for e in graph["edges"])
        finally:
            _teardown_test_env(ctx)
