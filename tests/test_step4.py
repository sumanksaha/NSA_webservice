"""Tests for Step 4: Open/Pending Action views + Adjudication linkage"""

import os
import sys
from datetime import date, datetime, timedelta

import pytest

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from flask import Flask

from app.extensions import db
from app.models import FSO, Adjudication, Inspection


@pytest.fixture
def app():
    """Create and configure a test Flask app."""
    app = Flask(__name__)

    # Use in-memory database for tests
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True

    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def test_fso(app):
    """Create a test FSO for use in tests."""
    with app.app_context():
        fso = FSO(fso_name="Test FSO")
        db.session.add(fso)
        db.session.commit()
        return fso


class TestDerivedStateQueries:
    """Tests for derived-state query logic."""

    def test_open_issues_query(self, app, test_fso):
        """Test Open Issues query: compliance_deadline >= today AND is_dismissed = false AND adjudication_id IS NULL."""
        with app.app_context():
            today = date.today()
            future_date = today + timedelta(days=30)
            past_date = today - timedelta(days=30)

            # Create test inspections
            # Open Issue (future deadline, not dismissed, no adjudication)
            open1 = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=future_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Pending Action (past deadline, not dismissed, no adjudication)
            pending = Inspection(
                inspection_code="INSP-2026-00002",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Dismissed (past deadline, dismissed, no adjudication)
            dismissed = Inspection(
                inspection_code="INSP-2026-00003",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=True,
                dismissed_by="Test FSO",
                dismissed_at=datetime.utcnow(),
                adjudication_id=None,
            )

            # Adjudicated (past deadline, not dismissed, has adjudication)
            adj = Adjudication(
                case_number="CASE-2026-001",
                food_safety_officer="Test FSO",
                fbo_owner="Test Owner",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                fssai_license="12345",
                First_inspection_date=past_date,
                compliance_deadline=past_date,
                inspection_date=past_date,
            )
            db.session.add(adj)
            db.session.commit()

            adjudicated = Inspection(
                inspection_code="INSP-2026-00004",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=adj.id,
            )

            db.session.add_all([open1, pending, dismissed, adjudicated])
            db.session.commit()

            # Query for Open Issues
            open_issues = Inspection.query.filter(
                Inspection.compliance_deadline >= today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            assert len(open_issues) == 1
            assert open_issues[0].inspection_code == "INSP-2026-00001"

    def test_pending_action_query(self, app, test_fso):
        """Test Pending Action query: compliance_deadline < today AND is_dismissed = false AND adjudication_id IS NULL."""
        with app.app_context():
            today = date.today()
            future_date = today + timedelta(days=30)
            past_date = today - timedelta(days=30)

            # Create test inspections
            # Open Issue (future deadline, not dismissed, no adjudication)
            open1 = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=future_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Pending Action (past deadline, not dismissed, no adjudication)
            pending = Inspection(
                inspection_code="INSP-2026-00002",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Dismissed (past deadline, dismissed, no adjudication)
            dismissed = Inspection(
                inspection_code="INSP-2026-00003",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=True,
                dismissed_by="Test FSO",
                dismissed_at=datetime.utcnow(),
                adjudication_id=None,
            )

            # Adjudicated (past deadline, not dismissed, has adjudication)
            adj = Adjudication(
                case_number="CASE-2026-001",
                food_safety_officer="Test FSO",
                fbo_owner="Test Owner",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                fssai_license="12345",
                First_inspection_date=past_date,
                compliance_deadline=past_date,
                inspection_date=past_date,
            )
            db.session.add(adj)
            db.session.commit()

            adjudicated = Inspection(
                inspection_code="INSP-2026-00004",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=adj.id,
            )

            db.session.add_all([open1, pending, dismissed, adjudicated])
            db.session.commit()

            # Query for Pending Action
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            assert len(pending_actions) == 1
            assert pending_actions[0].inspection_code == "INSP-2026-00002"

    def test_boundary_deadline_exactly_today(self, app, test_fso):
        """Test that inspections with deadline exactly = today are in Open Issues, not Pending Action."""
        with app.app_context():
            today = date.today()

            # Inspection with deadline exactly today
            today_deadline = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=today,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Inspection with deadline yesterday
            yesterday = today - timedelta(days=1)
            yesterday_deadline = Inspection(
                inspection_code="INSP-2026-00002",
                fso_name="Test FSO",
                inspection_date=yesterday,
                compliance_deadline=yesterday,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Inspection with deadline tomorrow
            tomorrow = today + timedelta(days=1)
            tomorrow_deadline = Inspection(
                inspection_code="INSP-2026-00003",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=tomorrow,
                is_dismissed=False,
                adjudication_id=None,
            )

            db.session.add_all([today_deadline, yesterday_deadline, tomorrow_deadline])
            db.session.commit()

            # Query for Open Issues (>= today)
            open_issues = Inspection.query.filter(
                Inspection.compliance_deadline >= today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            # Should include today and tomorrow
            open_codes = {i.inspection_code for i in open_issues}
            assert "INSP-2026-00001" in open_codes  # today
            assert "INSP-2026-00003" in open_codes  # tomorrow
            assert "INSP-2026-00002" not in open_codes  # yesterday

            # Query for Pending Action (< today)
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            # Should only include yesterday
            pending_codes = {i.inspection_code for i in pending_actions}
            assert "INSP-2026-00002" in pending_codes  # yesterday
            assert "INSP-2026-00001" not in pending_codes  # today
            assert "INSP-2026-00003" not in pending_codes  # tomorrow


class TestDismissAction:
    """Tests for Dismiss action state transitions."""

    def test_dismiss_sets_fields(self, app, test_fso):
        """Test that dismiss action sets is_dismissed, dismissed_by, and dismissed_at."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create a Pending Action inspection
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )
            db.session.add(inspection)
            db.session.commit()

            # Simulate dismiss action
            inspection.is_dismissed = True
            inspection.dismissed_by = "Test FSO"
            inspection.dismissed_at = datetime.utcnow()
            db.session.commit()

            # Verify fields are set
            result = Inspection.query.get(inspection.id)
            assert result.is_dismissed
            assert result.dismissed_by == "Test FSO"
            assert result.dismissed_at is not None

    def test_dismiss_removes_from_pending_action(self, app, test_fso):
        """Test that dismissed inspections are removed from Pending Action view."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create a Pending Action inspection
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )
            db.session.add(inspection)
            db.session.commit()

            # Verify it's in Pending Action
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()
            assert len(pending_actions) == 1

            # Dismiss the inspection
            inspection.is_dismissed = True
            inspection.dismissed_by = "Test FSO"
            inspection.dismissed_at = datetime.utcnow()
            db.session.commit()

            # Verify it's no longer in Pending Action
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()
            assert len(pending_actions) == 0

    def test_dismiss_open_inspection_fails(self, app, test_fso):
        """Test that dismissing an Open Issue (deadline not passed) is not allowed."""
        with app.app_context():
            today = date.today()
            today_dt = datetime.combine(today, datetime.min.time())
            future_date = today + timedelta(days=30)
            datetime.combine(future_date, datetime.min.time())

            # Create an Open Issue inspection
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=future_date,
                is_dismissed=False,
                adjudication_id=None,
            )
            db.session.add(inspection)
            db.session.commit()

            # Verify it's an Open Issue (deadline >= today)
            assert inspection.compliance_deadline >= today_dt

            # Dismiss should not be allowed - this is a business rule
            # In the implementation, the route checks this
            # Here we just verify the condition
            is_pending_action = (
                inspection.compliance_deadline < today_dt
                and not inspection.is_dismissed
                and inspection.adjudication_id is None
            )
            assert not is_pending_action  # Not a Pending Action, so cannot dismiss


class TestAdjudicationLinkage:
    """Tests for Create Adjudication action and linkage."""

    def test_adjudication_linkage_removes_from_views(self, app, test_fso):
        """Test that linking to adjudication removes inspection from both Open and Pending views."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create an adjudication
            adj = Adjudication(
                case_number="CASE-2026-001",
                food_safety_officer="Test FSO",
                fbo_owner="Test Owner",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                fssai_license="12345",
                First_inspection_date=past_date,
                compliance_deadline=past_date,
                inspection_date=past_date,
            )
            db.session.add(adj)
            db.session.commit()

            # Create a Pending Action inspection
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )
            db.session.add(inspection)
            db.session.commit()

            # Verify it's in Pending Action
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()
            assert len(pending_actions) == 1

            # Link to adjudication
            inspection.adjudication_id = adj.id
            db.session.commit()

            # Verify it's no longer in Pending Action
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()
            assert len(pending_actions) == 0

            # Verify it's also not in Open Issues
            open_issues = Inspection.query.filter(
                Inspection.compliance_deadline >= today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()
            assert len(open_issues) == 0

    def test_adjudication_prefill_data(self, app, test_fso):
        """Test that prefill data from inspection is correctly passed to adjudication."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create an inspection with full data
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                fssai_license="1234567890",
                ce_license_no="CE123456",
                fbo_name="Test FBO Name",
                fbo_address="Test FBO Address",
                concerned_food="Test Food",
                problem="Test Problem",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )
            db.session.add(inspection)
            db.session.commit()

            # Simulate the prefill data that would be passed
            prefill = {
                "from_inspection": str(inspection.id),
                "food_safety_officer": inspection.fso_name,
                "fbo_name": inspection.fbo_name,
                "fbo_address": inspection.fbo_address,
                "fssai_license": inspection.fssai_license,
                "ce_license_no": inspection.ce_license_no,
                "First_inspection_date": inspection.inspection_date,
                "compliance_deadline": inspection.compliance_deadline,
                "inspection_date": inspection.inspection_date,
                "concerned_food": inspection.concerned_food,
                "problem": inspection.problem,
            }

            # Verify all expected fields are present
            assert prefill["from_inspection"] == str(inspection.id)
            assert prefill["food_safety_officer"] == "Test FSO"
            assert prefill["fbo_name"] == "Test FBO Name"
            assert prefill["fbo_address"] == "Test FBO Address"
            assert prefill["fssai_license"] == "1234567890"
            assert prefill["ce_license_no"] == "CE123456"
            assert prefill["concerned_food"] == "Test Food"
            assert prefill["problem"] == "Test Problem"


class TestDaysOverdueCalculation:
    """Tests for days overdue calculation."""

    def test_days_overdue_calculation(self, app, test_fso):
        """Test that days overdue is calculated correctly."""
        with app.app_context():
            today = date.today()
            today_dt = datetime.combine(today, datetime.min.time())

            # Deadline 5 days ago
            deadline_5 = today - timedelta(days=5)
            datetime.combine(deadline_5, datetime.min.time())
            inspection_5 = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=deadline_5,
                compliance_deadline=deadline_5,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Deadline 10 days ago
            deadline_10 = today - timedelta(days=10)
            datetime.combine(deadline_10, datetime.min.time())
            inspection_10 = Inspection(
                inspection_code="INSP-2026-00002",
                fso_name="Test FSO",
                inspection_date=deadline_10,
                compliance_deadline=deadline_10,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Deadline yesterday
            deadline_1 = today - timedelta(days=1)
            datetime.combine(deadline_1, datetime.min.time())
            inspection_1 = Inspection(
                inspection_code="INSP-2026-00003",
                fso_name="Test FSO",
                inspection_date=deadline_1,
                compliance_deadline=deadline_1,
                is_dismissed=False,
                adjudication_id=None,
            )

            db.session.add_all([inspection_5, inspection_10, inspection_1])
            db.session.commit()

            # Query and calculate days overdue
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today_dt,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            for inspection in pending_actions:
                # compliance_deadline is already a datetime; extract .date() for comparison
                deadline = inspection.compliance_deadline.date()
                days_overdue = (today - deadline).days
                inspection.days_overdue = days_overdue

            # Verify calculations
            for inspection in pending_actions:
                if inspection.inspection_code == "INSP-2026-00001":
                    assert inspection.days_overdue == 5
                elif inspection.inspection_code == "INSP-2026-00002":
                    assert inspection.days_overdue == 10
                elif inspection.inspection_code == "INSP-2026-00003":
                    assert inspection.days_overdue == 1


class TestHistoryView:
    """Tests for History view (dismissed + adjudicated)."""

    def test_history_includes_dismissed(self, app, test_fso):
        """Test that History view includes dismissed inspections."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create a dismissed inspection
            dismissed = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=True,
                dismissed_by="Test FSO",
                dismissed_at=datetime.utcnow(),
                adjudication_id=None,
            )
            db.session.add(dismissed)
            db.session.commit()

            # Query for History
            history = Inspection.query.filter(
                (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
            ).all()

            assert len(history) >= 1
            assert any(i.inspection_code == "INSP-2026-00001" for i in history)

    def test_history_includes_adjudicated(self, app, test_fso):
        """Test that History view includes adjudicated inspections."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create an adjudication
            adj = Adjudication(
                case_number="CASE-2026-001",
                food_safety_officer="Test FSO",
                fbo_owner="Test Owner",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                fssai_license="12345",
                First_inspection_date=past_date,
                compliance_deadline=past_date,
                inspection_date=past_date,
            )
            db.session.add(adj)
            db.session.commit()

            # Create an adjudicated inspection
            adjudicated = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=adj.id,
            )
            db.session.add(adjudicated)
            db.session.commit()

            # Query for History
            history = Inspection.query.filter(
                (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
            ).all()

            assert len(history) >= 1
            assert any(i.inspection_code == "INSP-2026-00001" for i in history)

    def test_history_excludes_open_and_pending(self, app, test_fso):
        """Test that History view excludes Open Issues and Pending Action."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)
            future_date = today + timedelta(days=30)

            # Create Open Issue
            open_issue = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=today,
                compliance_deadline=future_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            # Create Pending Action
            pending = Inspection(
                inspection_code="INSP-2026-00002",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=False,
                adjudication_id=None,
            )

            db.session.add_all([open_issue, pending])
            db.session.commit()

            # Query for History
            history = Inspection.query.filter(
                (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
            ).all()

            # Should not include open or pending
            history_codes = {i.inspection_code for i in history}
            assert "INSP-2026-00001" not in history_codes
            assert "INSP-2026-00002" not in history_codes


class TestPrecedenceRules:
    """Tests for precedence: adjudication_id takes precedence over is_dismissed."""

    def test_adjudication_overrides_dismissal(self, app, test_fso):
        """Test that an inspection with both adjudication_id and is_dismissed=True is treated as adjudicated."""
        with app.app_context():
            today = date.today()
            past_date = today - timedelta(days=30)

            # Create an adjudication
            adj = Adjudication(
                case_number="CASE-2026-001",
                food_safety_officer="Test FSO",
                fbo_owner="Test Owner",
                fbo_name="Test FBO",
                fbo_address="Test Address",
                fssai_license="12345",
                First_inspection_date=past_date,
                compliance_deadline=past_date,
                inspection_date=past_date,
            )
            db.session.add(adj)
            db.session.commit()

            # Create inspection that is both dismissed and adjudicated
            # (This shouldn't happen in practice, but test the query behavior)
            inspection = Inspection(
                inspection_code="INSP-2026-00001",
                fso_name="Test FSO",
                inspection_date=past_date,
                compliance_deadline=past_date,
                is_dismissed=True,
                dismissed_by="Test FSO",
                dismissed_at=datetime.utcnow(),
                adjudication_id=adj.id,
            )
            db.session.add(inspection)
            db.session.commit()

            # Query for History (should include it because of adjudication_id)
            history = Inspection.query.filter(
                (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
            ).all()

            assert any(i.inspection_code == "INSP-2026-00001" for i in history)

            # Query for Pending Action (should NOT include it)
            pending_actions = Inspection.query.filter(
                Inspection.compliance_deadline < today,
                ~Inspection.is_dismissed,
                Inspection.adjudication_id.is_(None),
            ).all()

            assert not any(i.inspection_code == "INSP-2026-00001" for i in pending_actions)
