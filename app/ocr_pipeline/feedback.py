"""Few-shot example store refresh (Phase D feedback loop).

Shared synchronous implementation used by both the Celery task
(:func:`app.ocr_pipeline.tasks.refresh_few_shot_examples`) and the
dashboard's manual trigger route — one store, two triggers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FEW_SHOT_FILENAME = "few_shot_examples.json"


def refresh_few_shot_examples_sync(limit: int = 50) -> dict:
    """Rebuild ``instance/ocr/few_shot_examples.json`` from recent corrections.

    Groups the most recent human-reviewed old→new value pairs by field name;
    the Vision-LLM extraction prompts consume them as few-shot examples so
    repeated mistakes get corrected at the source.

    Requires an active Flask app context (reads ``current_app.instance_path``).
    """
    from flask import current_app

    from app.extensions import db
    from app.models import OCRCorrection

    rows = (
        db.session.query(OCRCorrection)
        .filter(OCRCorrection.field_name.notlike("lab:%"))
        .order_by(OCRCorrection.created_at.desc())
        .limit(limit)
        .all()
    )

    examples: dict[str, list[dict]] = {}
    for row in rows:
        examples.setdefault(row.field_name, []).append({
            "wrong": row.old_value,
            "right": row.new_value,
        })

    out_dir = Path(current_app.instance_path) / "ocr"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / FEW_SHOT_FILENAME
    out_path.write_text(json.dumps(examples, indent=2), encoding="utf-8")

    total = sum(len(v) for v in examples.values())
    logger.info(
        "refresh_few_shot_examples_sync: wrote %d examples across %d fields -> %s",
        total,
        len(examples),
        out_path,
    )
    return {"examples": total, "fields": len(examples), "path": str(out_path)}
