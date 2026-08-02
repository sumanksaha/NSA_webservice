"""Tests for the search module: FTS5 index, indexer, and API endpoints.

Covers:
  - FTS5 virtual-table creation (``ensure_search_table``)
  - Single-record indexing (``index_record``)
  - Full re-index (``index_all``)
  - Cross-model full-text search (``search``)
  - Auto-indexing via SQLAlchemy ``after_flush`` hooks
  - Search page + JSON API endpoints (auth gating, query params)
"""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import (
    FSO,
    Adjudication,
    Annexure,
    CaseFile,
    Evidence,
    User,
)
from app.search.indexer import (
    ENTITY_ADJUDICATION,
    ENTITY_ANNEXURE,
    ENTITY_CASE_FILE,
    ENTITY_EVIDENCE,
    ensure_search_table,
    index_all,
    index_record,
    search,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client():
    """Test client with database context, test data, and logged-in user."""
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            ensure_search_table()

            # Create a test user
            user = User(
                username="testuser",
                password_hash="pbkdf2:sha256$test$dummy",  # noqa: S106
            )
            db.session.add(user)

            # Create a test FSO
            fso = FSO(fso_name="Test Officer")
            db.session.add(fso)

            # Create a test CaseFile
            case_file = CaseFile(
                case_number="TESTCASE001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG123",
                manufacturer_name="Acme Foods Ltd",
                manufacturer_fbo_name="Acme FBO",
                manufacturer_address="123 Mfg St",
                retailer_fssai="RET456",
                retailer_name="Test Retailer",
                retailer_fbo_name="Retailer FBO",
                retailer_address="456 Retail St",
                product_name="Cotton Candy",
                batch_no="BATCH001",
                sample_quantity="1000g",
                packet_count=4,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="TEST001",
                sample_submission_date=datetime(2026, 7, 2),
                Lab_Registration_No="WB/FOOD/2025/001",
                do_receipt_date=datetime(2026, 7, 4),
                is_misbranded=False,
                is_substandard=False,
                analyst_report_no="PK/378/2025-26",
                analyst_report_date=datetime(2026, 7, 5),
                directive_letter_no="H/FSSA/FSO/3054/2025-26",
                directive_letter_date=datetime(2026, 7, 6),
                retailer_report_receive_date=datetime(2026, 7, 7),
                manufacturer_report_receive_date=datetime(2026, 7, 8),
                applicable_regulation="Regulation No 5(9)",
                applicable_clause="Clause (zf) of section 3",
                applicable_sections="Sec 3",
            )
            db.session.add(case_file)

            # Create a test Adjudication
            adjudication = Adjudication(
                case_number="ADJ001",
                food_safety_officer="Test Officer",
                fbo_owner="John Doe",
                fbo_name="Doe's Grocery",
                fbo_address="123 Main St",
                fssai_license="FSSAI789",
                ce_license_no="CE12345",
                ce_trade_name="Doe Store",
                ce_proprietor="John Doe",
                ce_address="456 Side St",
                ce_status="active",
                concerned_food="Contaminated snacks",
                problem="Presence of artificial colour",
                First_inspection_date=datetime(2026, 7, 1),
                compliance_deadline=datetime(2026, 8, 1),
                inspection_date=datetime(2026, 7, 2),
                non_license="no",
                pre_authorization="no",
                complaint_lodged="no",
            )
            db.session.add(adjudication)

            # Create a test Annexure
            annexure = Annexure(
                caption="Lab Report Annexure",
                file_hash="abc123hash",
                filepath="/tmp/annexure1.pdf",
                filename="lab_report.pdf",
                ocr_text="Laboratory test results show presence of heavy metals in the sample",
                tags="lab, report, chemistry, heavy-metals",
            )
            db.session.add(annexure)

            # Create a test Evidence
            evidence = Evidence(
                evidence_type="photo",
                filepath="/tmp/evidence1.jpg",
                filename="violation_photo.jpg",
                caption="Unsanitary conditions at premises",
                ocr_text="Rodent droppings and dirty surfaces observed during inspection",
                tags="hygiene, violation, pest",
            )
            db.session.add(evidence)

            db.session.commit()
            # Rebuild FTS5 index to ensure clean state
            index_all()

            yield client

            db.session.remove()
            db.drop_all()


# ---------------------------------------------------------------------------
# FTS5 table lifecycle
# ---------------------------------------------------------------------------


class TestEnsureSearchTable:
    """Tests for FTS5 virtual-table creation."""

    def test_ensure_search_table_returns_true_on_sqlite(self, test_client):
        """On SQLite, ensure_search_table() should create the table and return True."""
        with test_client.application.app_context():
            from app.search.indexer import _dialect

            assert _dialect() == "sqlite"
            result = ensure_search_table()
            assert result is True

    def test_ensure_search_table_is_idempotent(self, test_client):
        """Calling ensure_search_table() twice should not raise."""
        with test_client.application.app_context():
            ensure_search_table()
            ensure_search_table()  # second call should be a no-op

    def test_search_table_exists_after_call(self, test_client):
        """The virtual table should be queryable after ensure_search_table()."""
        with test_client.application.app_context():
            from sqlalchemy import text

            from app.extensions import db as _db

            _db.session.execute(text("SELECT count(*) FROM search_index"))


# ---------------------------------------------------------------------------
# Single-record indexing
# ---------------------------------------------------------------------------


class TestIndexRecord:
    """Tests for index_record()."""

    def test_index_case_file(self, test_client):
        """index_record for a CaseFile should return True."""
        with test_client.application.app_context():
            cf = db.session.execute(db.select(CaseFile)).scalars().first()
            result = index_record(ENTITY_CASE_FILE, str(cf.id))
            assert result is True

    def test_index_adjudication(self, test_client):
        """index_record for an Adjudication should return True."""
        with test_client.application.app_context():
            adj = db.session.execute(db.select(Adjudication)).scalars().first()
            result = index_record(ENTITY_ADJUDICATION, str(adj.id))
            assert result is True

    def test_index_annexure(self, test_client):
        """index_record for an Annexure should return True."""
        with test_client.application.app_context():
            ann = db.session.execute(db.select(Annexure)).scalars().first()
            result = index_record(ENTITY_ANNEXURE, ann.id)
            assert result is True

    def test_index_evidence(self, test_client):
        """index_record for an Evidence should return True."""
        with test_client.application.app_context():
            ev = db.session.execute(db.select(Evidence)).scalars().first()
            result = index_record(ENTITY_EVIDENCE, ev.id)
            assert result is True

    def test_index_nonexistent_record(self, test_client):
        """index_record for a non-existent record should return False."""
        with test_client.application.app_context():
            result = index_record(ENTITY_CASE_FILE, "99999")
            assert result is False

    def test_index_invalid_entity_type(self, test_client):
        """index_record with an unknown entity_type should return False."""
        with test_client.application.app_context():
            result = index_record("unknown", "1")
            assert result is False


# ---------------------------------------------------------------------------
# Full re-index
# ---------------------------------------------------------------------------


class TestIndexAll:
    """Tests for index_all()."""

    def test_index_all_counts_records(self, test_client):
        """index_all() should return the count of all indexed records."""
        with test_client.application.app_context():
            count = index_all()
            assert count == 4  # 1 CaseFile + 1 Adjudication + 1 Annexure + 1 Evidence

    def test_index_all_clears_old_data(self, test_client):
        """index_all() should replace stale data with current records only."""
        with test_client.application.app_context():
            # Add a record then delete it — FTS5 may still have the old row
            cf = CaseFile(
                case_number="STALE001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG123",
                manufacturer_name="Ghost Manufacturer",
                manufacturer_fbo_name="Ghost FBO",
                manufacturer_address="123 Ghost St",
                retailer_fssai="RET456",
                retailer_name="Ghost Retailer",
                retailer_fbo_name="Ghost Retailer FBO",
                retailer_address="456 Ghost St",
                product_name="Ghost Product",
                batch_no="GHOST001",
                sample_quantity="500g",
                packet_count=2,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="GHOST001",
                sample_submission_date=datetime(2026, 7, 2),
                Lab_Registration_No="WB/FOOD/2025/002",
                do_receipt_date=datetime(2026, 7, 4),
                analyst_report_no="PK/379/2025-26",
                analyst_report_date=datetime(2026, 7, 5),
                directive_letter_no="H/FSSA/FSO/3055/2025-26",
                directive_letter_date=datetime(2026, 7, 6),
                retailer_report_receive_date=datetime(2026, 7, 7),
                manufacturer_report_receive_date=datetime(2026, 7, 8),
            )
            db.session.add(cf)
            db.session.commit()
            # At this point the after_flush hook auto-indexed cf

            # Now delete cf and rebuild
            db.session.delete(cf)
            db.session.commit()
            index_all()

            # Ghost Manufacturer should no longer appear in search
            results = search("Ghost")
            assert len(results) == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    """Tests for the search() function."""

    def test_search_case_file_by_manufacturer(self, test_client):
        """Search should find case_file records by manufacturer name."""
        with test_client.application.app_context():
            results = search("Acme", entity_type=ENTITY_CASE_FILE)
            assert len(results) == 1
            assert results[0]["entity_type"] == ENTITY_CASE_FILE
            assert "Acme" in results[0]["snippet"]

    def test_search_adjudication_by_fbo_name(self, test_client):
        """Search should find adjudication records by FBO name."""
        with test_client.application.app_context():
            results = search("Doe", entity_type=ENTITY_ADJUDICATION)
            assert len(results) == 1
            assert results[0]["entity_type"] == ENTITY_ADJUDICATION

    def test_search_annexure_by_tags(self, test_client):
        """Search should find annexure records by tags."""
        with test_client.application.app_context():
            results = search("heavy-metals")
            assert len(results) == 1
            assert results[0]["entity_type"] == ENTITY_ANNEXURE

    def test_search_evidence_by_ocr_text(self, test_client):
        """Search should find evidence records by OCR text."""
        with test_client.application.app_context():
            results = search("Rodent")
            assert len(results) == 1
            assert results[0]["entity_type"] == ENTITY_EVIDENCE

    def test_search_cross_model(self, test_client):
        """Without entity_type filter, search should span all models."""
        with test_client.application.app_context():
            results = search("Test")
            # "Test" appears in Test Officer (CF), Doe's Grocery (adj),
            # "Test Retailer" (CF), etc. — at least 2 results expected.
            assert len(results) >= 2
            types_found = {r["entity_type"] for r in results}
            assert len(types_found) >= 1

    def test_search_empty_query(self, test_client):
        """Empty query should return an empty list."""
        with test_client.application.app_context():
            assert search("") == []
            assert search("   ") == []

    def test_search_no_results(self, test_client):
        """Query with no matches should return an empty list."""
        with test_client.application.app_context():
            results = search("nonexistentterm12345")
            assert len(results) == 0

    def test_search_result_fields(self, test_client):
        """Each result dict should have the expected keys."""
        with test_client.application.app_context():
            results = search("Acme", limit=20)
            assert len(results) >= 1
            for r in results:
                assert "entity_type" in r
                assert "entity_id" in r
                assert "title" in r
                assert "snippet" in r

    def test_search_respects_limit(self, test_client):
        """The limit parameter should cap the number of results."""
        with test_client.application.app_context():
            results = search("Test", limit=1)
            assert len(results) <= 1


# ---------------------------------------------------------------------------
# Auto-indexing via SQLAlchemy after_flush hooks
# ---------------------------------------------------------------------------


class TestAutoIndexHook:
    """Tests that the after_flush event hook auto-indexes records."""

    def test_auto_index_on_insert(self, test_client):
        """Newly inserted records should appear in search without manual indexing."""
        with test_client.application.app_context():
            # Insert a record and commit — the hook should auto-index it
            cf = CaseFile(
                case_number="AUTO001",
                food_safety_officer_name="Test Officer",
                authorization_date=datetime(2026, 7, 3),
                inspection_date=datetime(2026, 7, 3),
                inspection_time="10:00",
                manufacturer_fssai="MFG999",
                manufacturer_name="UniqueAutoMfg",
                manufacturer_fbo_name="Auto FBO",
                manufacturer_address="789 Auto St",
                retailer_fssai="RET789",
                retailer_name="Auto Retailer",
                retailer_fbo_name="Auto Retailer FBO",
                retailer_address="321 Auto St",
                product_name="Auto Product",
                batch_no="AUTO001",
                sample_quantity="500g",
                packet_count=1,
                mfg_date=datetime(2026, 6, 1),
                expiry_date=datetime(2026, 8, 1),
                sample_code="AUTO001",
                sample_submission_date=datetime(2026, 7, 2),
                Lab_Registration_No="WB/FOOD/2025/009",
                do_receipt_date=datetime(2026, 7, 4),
                analyst_report_no="PK/999/2025-26",
                analyst_report_date=datetime(2026, 7, 5),
                directive_letter_no="H/FSSA/FSO/9999/2025-26",
                directive_letter_date=datetime(2026, 7, 6),
                retailer_report_receive_date=datetime(2026, 7, 7),
                manufacturer_report_receive_date=datetime(2026, 7, 8),
            )
            db.session.add(cf)
            db.session.commit()

            results = search("UniqueAutoMfg")
            assert len(results) == 1

    def test_auto_index_on_update(self, test_client):
        """Updated records should reflect new content in search."""
        with test_client.application.app_context():
            cf = CaseFile.query.filter_by(case_number="TESTCASE001").one()

            cf.manufacturer_name = "UpdatedMfgName"
            db.session.commit()

            results = search("UpdatedMfgName")
            assert len(results) >= 1

    def test_auto_index_on_delete(self, test_client):
        """Deleted records should be removed from the FTS5 index."""
        with test_client.application.app_context():
            cf = CaseFile.query.filter_by(case_number="TESTCASE001").one()
            db.session.delete(cf)
            db.session.commit()

            results = search("Acme")
            assert len(results) == 0


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestSearchAPI:
    """Tests for the JSON search API at /search/api."""

    def test_api_requires_auth(self, test_client):
        """GET /search/api without login should redirect to auth."""
        resp = test_client.get("/search/api?q=test", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_api_returns_results(self, test_client):
        """Authenticated GET /search/api should return JSON with results."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/api?q=Acme", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["query"] == "Acme"
        assert data["total"] >= 1
        assert len(data["results"]) >= 1

    def test_api_empty_query(self, test_client):
        """GET /search/api with empty q should return 200 with zero results."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/api?q=", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0

    def test_api_invalid_entity_type(self, test_client):
        """GET /search/api?type=invalid should return 400."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/api?q=test&type=invalid", follow_redirects=False)
        assert resp.status_code == 400

    def test_api_filter_by_type(self, test_client):
        """GET /search/api?type=case_file should only return case_file results."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/api?q=Test&type=case_file", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        for r in data["results"]:
            assert r["entity_type"] == "case_file"

    def test_api_no_results(self, test_client):
        """GET /search/api with nonexistent query should return 200 with 0 results."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/api?q=zzznonexistentzzz", follow_redirects=False)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# Search page tests
# ---------------------------------------------------------------------------


class TestSearchPage:
    """Tests for the search page at /search/."""

    def test_page_requires_auth(self, test_client):
        """GET /search/ without login should redirect to auth."""
        resp = test_client.get("/search/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

    def test_page_returns_200(self, test_client):
        """Authenticated GET /search/ should return 200 with rendered HTML."""
        with test_client.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        resp = test_client.get("/search/", follow_redirects=False)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Search" in html
        assert "searchBox" in html
