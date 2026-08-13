"""Phase 3 — compute metrics and write all 10 deliverables.

Usage:
    python -m evaluation.run_report [--only retrieval]

Reads the cached raw results under ``evaluation/out/raw`` (produced by
``run_retrieval`` and ``run_generation``), computes every metric and writes
the deliverables under ``evaluation/out/``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.report")


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.config import OUT_DIR
    from evaluation.report import (
        deliverable_10,
        deliverable_4,
        deliverable_5,
        deliverables_1_3,
        deliverables_6_7,
        prepare,
        write_fusion_validation,
    )
    from evaluation.report_md import (
        write_kg_report,
        write_main_report,
        write_readiness,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["retrieval", "answers", "all"], default="all")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from app import create_app

    app = create_app()
    with app.app_context():
        data = prepare()

        # schema report for the md
        from evaluation.benchmark import schema_report

        data["_schema_report"] = schema_report()
        data["_kg_agg"] = _kg_aggregate(data)
        data["_bottlenecks"] = _bottlenecks(data)
        data["_label_counts"] = _label_counts(data)

        if args.only in ("all", "retrieval"):
            deliverables_1_3(data)
            deliverables_6_7(data)
        if args.only in ("all", "answers"):
            deliverable_4(data)
            deliverable_5(data)
        deliverable_10(data)
        write_fusion_validation(data)
        write_main_report(data)
        write_kg_report(data)
        write_readiness(data)
        logger.info("deliverables written to %s", OUT_DIR)
    return 0


def _kg_aggregate(data: dict) -> dict:
    from evaluation.config import OUT_DIR
    import json as _json

    path = OUT_DIR / "aggregate_metrics.json"
    if path.exists():
        try:
            return _json.loads(path.read_text(encoding="utf-8")).get("kg", {})
        except Exception:
            pass
    vals = list(data["kg_inc"].values())
    n = max(len(vals), 1)
    return {
        "help_rate": sum(1 for v in vals if v["kg_helped"]) / n,
        "harm_rate": sum(1 for v in vals if v["kg_harm"]) / n,
    }


def _bottlenecks(data: dict) -> list[dict]:
    from evaluation.failures import bottleneck_tally

    return bottleneck_tally(list(data["failures"].values()))


def _label_counts(data: dict) -> dict:
    from evaluation.failures import label_tally

    return label_tally(list(data["failures"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
