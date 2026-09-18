"""Regenerate just the markdown report from the saved Experiment A JSON."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.experiment_a_gold_rank import write_markdown_report, BUCKETS

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_JSON = OUT_DIR / "experiment_a_gold_rank.json"
OUT_MD = OUT_DIR / "experiment_a_gold_rank_report.md"

output = json.loads(OUT_JSON.read_text(encoding="utf-8"))
prev_file = OUT_DIR / "ce_v2_checkpoint_eval.json"
prev_data = json.loads(prev_file.read_text())
prev_v2 = prev_data["results"]["ce_v2_K500"]
consistency = output["consistency_check"]
write_markdown_report(output, OUT_MD, prev_v2, consistency)
print(f"Report written to: {OUT_MD}")
