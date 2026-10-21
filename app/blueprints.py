"""Blueprint registration for the Flask app.

Extracted from ``app/__init__.py`` so the application factory is not edited
for every new blueprint (Shotgun Surgery finding, 2026-09-12 review). Add new
blueprints here; the factory calls :func:`register_blueprints`.
"""

from __future__ import annotations

from flask import Blueprint, Flask


def register_blueprints(app: Flask) -> None:
    """Register every application blueprint on ``app``.

    Order matters: ``auth`` first so the login page is available as soon as
    the app can serve requests.
    """
    # Import blueprints lazily so the app can boot without optional deps.
    from app.adjudication.routes import adjudication_bp as _adjudication_bp
    from app.annexure import annexure_bp as _annexure_bp
    from app.audit import audit_bp as _audit_bp
    from app.auditor import auditor_bp as _auditor_bp
    from app.auth.routes import auth_bp as _auth_bp
    from app.bill_generator.routes import bill_generator_bp as _bill_generator_bp
    from app.billing.routes import billing_bp as _billing_bp
    from app.case_file_generator.routes import case_file_generator_bp as _case_file_generator_bp
    from app.document_viewer import document_viewer_bp as _document_viewer_bp
    from app.evidence import evidence_bp as _evidence_bp
    from app.fbo_issue.routes import fbo_issue_bp as _fbo_issue_bp
    from app.food_cell import food_cell_bp as _food_cell_bp
    from app.health import health_bp as _health_bp
    from app.inspection.routes import inspection_bp as _inspection_bp
    from app.knowledge_graph import kg_bp as _kg_bp
    from app.legal_analysis import legal_analysis_bp as _legal_analysis_bp
    from app.notepad import notepad_bp as _notepad_bp
    from app.sample.routes import sample_bp as _sample_bp
    from app.search import search_bp as _search_bp
    from app.settings.routes import settings_bp as _settings_bp
    from app.sync import sync_bp as _sync_bp
    from app.tasks_webhook import tasks_webhook_bp as _tasks_webhook_bp
    from app.timeline import timeline_bp as _timeline_bp
    from app.validation import validation_bp as _validation_bp
    from app.version_control import version_control_bp as _version_control_bp
    from app.case_intelligence import intelligence_bp as _intelligence_bp
    from app.ai_assistant import ai_bp as _ai_bp
    from app.workdiary import workdiary_bp as _workdiary_bp
    from app.diary import diary_bp as _diary_bp
    from app.schedule import schedule_bp as _schedule_bp
    from app.rag import rag_bp as _rag_bp
    from app.analytics import analytics_bp as _analytics_bp
    from app.comments import comments_bp as _comments_bp
    from app.autopopulation import autopopulation_bp as _autopopulation_bp
    from app.conflict_resolution import conflict_resolution_bp as _conflict_resolution_bp
    from app.feedback_dashboard import feedback_dashboard_bp as _feedback_dashboard_bp
    from app.ocr_extraction import ocr_extraction_bp as _ocr_extraction_bp

    # ponytail: consolidated (blueprint, url_prefix) list — no more
    # mid-function ``from app.xxx import bp`` scattered between register calls.
    registered: list[tuple[Blueprint, str | None]] = [
        (_auth_bp, "/auth"),
        (_auditor_bp, "/auditor"),
        (_case_file_generator_bp, "/case_file_generator"),
        (_adjudication_bp, "/adjudication"),
        (_document_viewer_bp, "/document_viewer"),
        (_evidence_bp, "/evidence"),
        (_bill_generator_bp, "/bill_generator"),
        (_fbo_issue_bp, "/fbo-issue"),
        (_sample_bp, "/sample"),
        (_billing_bp, "/billing"),
        (_settings_bp, "/settings"),
        (_inspection_bp, "/inspection"),
        (_legal_analysis_bp, "/legal"),
        (_audit_bp, "/admin"),
        (_version_control_bp, None),
        (_tasks_webhook_bp, None),
        (_search_bp, "/search"),
        (_annexure_bp, "/annexure"),
        (_validation_bp, "/validation"),
        (_health_bp, None),
        (_food_cell_bp, "/food-cell"),
        (_kg_bp, "/knowledge-graph"),
        (_notepad_bp, "/notepad"),
        (_sync_bp, "/sync"),
        (_intelligence_bp, "/case-intelligence"),
        (_ai_bp, "/ai-assistant"),
        # timeline_bp carries its own url_prefix ("/timeline") in the Blueprint.
        (_timeline_bp, None),
        (_analytics_bp, "/analytics"),
        (_comments_bp, None),
        (_workdiary_bp, None),
        (_diary_bp, None),
        (_schedule_bp, None),
        (_rag_bp, None),
        (_ocr_extraction_bp, "/ocr"),
        (_conflict_resolution_bp, "/conflict-resolution"),
        (_autopopulation_bp, "/autopopulation"),
        (_feedback_dashboard_bp, "/feedback-dashboard"),
    ]

    for bp, prefix in registered:
        app.register_blueprint(bp, url_prefix=prefix)
