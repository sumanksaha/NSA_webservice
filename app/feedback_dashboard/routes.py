"""Feedback dashboard routes (Phase D)."""

from __future__ import annotations

from flask import jsonify, render_template

from app.extensions import db
from app.feedback_dashboard import feedback_dashboard_bp
from app.models import OCRCorrection, OCRDocument


def field_accuracy_metrics() -> dict:
    """Per-field extraction accuracy derived from correction history.

    ``accuracy = 1 - (documents corrected for the field / documents extracted)``.
    Fields never corrected report accuracy 1.0; fields absent from any document
    are not listed. Also returns the few-shot example count currently on file.
    """
    total_docs = db.session.query(OCRDocument).count()
    rows = (
        db.session.query(OCRCorrection.field_name, db.func.count(OCRCorrection.id))
        .group_by(OCRCorrection.field_name)
        .all()
    )

    per_field: dict[str, dict] = {}
    for field_name, corrections in rows:
        corrected_docs = (
            db.session.query(OCRCorrection.id)
            .filter(OCRCorrection.field_name == field_name)
            .distinct()
            .count()
        )
        accuracy = round(max(0.0, 1.0 - (corrected_docs / total_docs)), 4) if total_docs else 1.0
        per_field[field_name] = {
            "corrections": corrections,
            "corrected_documents": corrected_docs,
            "accuracy": accuracy,
        }

    return {
        "total_documents": total_docs,
        "fields": dict(sorted(per_field.items(), key=lambda kv: kv[1]["accuracy"])),
        "few_shot_examples": _count_few_shot_examples(),
    }


def _count_few_shot_examples() -> int:
    import json
    from pathlib import Path

    from flask import current_app

    path = Path(current_app.instance_path) / "ocr" / "few_shot_examples.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return sum(len(v) for v in data.values())
    except (OSError, ValueError):
        return 0


@feedback_dashboard_bp.route("/")
def dashboard():
    metrics = field_accuracy_metrics()
    worst = [
        {"field": name, **stats}
        for name, stats in list(metrics["fields"].items())[:10]
        if stats["accuracy"] < 1.0
    ]
    return render_template(
        "feedback_dashboard/index.html",
        metrics=metrics,
        worst_fields=worst,
    )


@feedback_dashboard_bp.route("/api/metrics")
def metrics_api():
    return jsonify(field_accuracy_metrics())


@feedback_dashboard_bp.route("/refresh-examples", methods=["POST"])
def refresh_examples():
    """Run the few-shot example rebuild synchronously (Celery fallback path).

    The Celery task wraps this same helper for async scheduling.
    """
    from app.ocr_pipeline.feedback import refresh_few_shot_examples_sync

    result = refresh_few_shot_examples_sync()
    return jsonify({"status": "ok", **result})
