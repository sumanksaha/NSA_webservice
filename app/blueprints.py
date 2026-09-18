"""Blueprint registration for the Flask app.

Extracted from ``app/__init__.py`` so the application factory is not edited
for every new blueprint (Shotgun Surgery finding, 2026-09-12 review). Add new
blueprints here; the factory calls :func:`register_blueprints`.
"""

from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    """Register every application blueprint on ``app``.

    Order matters: ``auth`` first so the login page is available as soon as
    the app can serve requests.
    """
    from app.adjudication.routes import adjudication_bp
    from app.annexure import annexure_bp
    from app.audit import audit_bp
    from app.auth.routes import auth_bp
    from app.bill_generator.routes import bill_generator_bp
    from app.billing.routes import billing_bp
    from app.case_file_generator.routes import case_file_generator_bp
    from app.fbo_issue.routes import fbo_issue_bp
    from app.food_cell import food_cell_bp
    from app.health import health_bp
    from app.inspection.routes import inspection_bp
    from app.knowledge_graph import kg_bp
    from app.legal_analysis import legal_analysis_bp
    from app.notepad import notepad_bp
    from app.sample.routes import sample_bp
    from app.search import search_bp
    from app.settings.routes import settings_bp
    from app.sync import sync_bp
    from app.tasks_webhook import tasks_webhook_bp
    from app.timeline import timeline_bp
    from app.validation import validation_bp
    from app.version_control import version_control_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(case_file_generator_bp, url_prefix="/case_file_generator")
    app.register_blueprint(adjudication_bp, url_prefix="/adjudication")
    from app.document_viewer import document_viewer_bp

    app.register_blueprint(document_viewer_bp, url_prefix="/document_viewer")
    from app.evidence import evidence_bp

    app.register_blueprint(evidence_bp, url_prefix="/evidence")
    app.register_blueprint(bill_generator_bp, url_prefix="/bill_generator")
    app.register_blueprint(fbo_issue_bp, url_prefix="/fbo-issue")
    app.register_blueprint(sample_bp, url_prefix="/sample")
    app.register_blueprint(billing_bp, url_prefix="/billing")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(inspection_bp, url_prefix="/inspection")
    app.register_blueprint(legal_analysis_bp, url_prefix="/legal")
    app.register_blueprint(audit_bp, url_prefix="/admin")
    app.register_blueprint(version_control_bp)
    app.register_blueprint(tasks_webhook_bp)
    app.register_blueprint(search_bp, url_prefix="/search")
    app.register_blueprint(annexure_bp, url_prefix="/annexure")
    app.register_blueprint(validation_bp, url_prefix="/validation")
    app.register_blueprint(health_bp)
    app.register_blueprint(food_cell_bp, url_prefix="/food-cell")
    app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")
    app.register_blueprint(notepad_bp, url_prefix="/notepad")
    app.register_blueprint(sync_bp, url_prefix="/sync")
    from app.case_intelligence import intelligence_bp

    app.register_blueprint(intelligence_bp, url_prefix="/case-intelligence")

    from app.ai_assistant import ai_bp

    app.register_blueprint(ai_bp, url_prefix="/ai-assistant")
    # timeline_bp carries its own url_prefix ("/timeline") in the Blueprint.
    app.register_blueprint(timeline_bp)
    # Analytics dashboard (Phase 15)
    from app.analytics import analytics_bp

    app.register_blueprint(analytics_bp, url_prefix="/analytics")
    # Comments API (Phase 18) — visibility inherited from the parent case
    from app.comments import comments_bp

    app.register_blueprint(comments_bp)

    # Work diary (accumulates Inspections per FSO; preview + PDF download)
    from app.workdiary import workdiary_bp

    app.register_blueprint(workdiary_bp)
    # RAG blueprint (Phase 1: retrieval foundation + health endpoint)
    from app.rag import rag_bp

    app.register_blueprint(rag_bp)

    # OCR pipeline Phases B–E (review workflow, conflicts, autopopulation, feedback)
    from app.autopopulation import autopopulation_bp
    from app.conflict_resolution import conflict_resolution_bp
    from app.feedback_dashboard import feedback_dashboard_bp
    from app.ocr_extraction import ocr_extraction_bp

    app.register_blueprint(ocr_extraction_bp, url_prefix="/ocr")
    app.register_blueprint(conflict_resolution_bp, url_prefix="/conflict-resolution")
    app.register_blueprint(autopopulation_bp, url_prefix="/autopopulation")
    app.register_blueprint(feedback_dashboard_bp, url_prefix="/feedback-dashboard")
