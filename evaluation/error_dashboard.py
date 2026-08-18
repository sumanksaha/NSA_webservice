"""Error-analysis dashboard — failure frequency, category breakdown, improvement attribution.

Reads the ranking-failure dataset and evaluation results to produce:
  1. Queries where gold is missing from K=500
  2. Queries where gold is present but ranked below 10
  3. Queries where gold is ranked 11-20
  4. Most common hard negatives
  5. Most common same-Act failures
  6. Most common same-section failures
  7. Temporal failures
  8. Cross-reference failures
  9. Multi-provision failures

Output:
  evaluation/out/ceiling_v5/error_dashboard.json
  evaluation/out/ceiling_v5/error_dashboard_report.md

Usage:
    python -m evaluation.error_dashboard
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
CACHE_DIR = PROJECT_ROOT / "evaluation" / "out" / "cache"
FAILURE_FILE = CACHE_DIR / "ranking_failures.jsonl"
MINING_FILE = CACHE_DIR / "hard_negative_mining.jsonl"
EVAL_FILE = OUT_DIR / "hard_neg_eval.json"
OUT_JSON = OUT_DIR / "error_dashboard.json"
OUT_MD = OUT_DIR / "error_dashboard_report.md"


def build_dashboard() -> dict:
    """Build the error analysis dashboard."""
    dashboard = {
        "failure_categories": {},
        "query_level_analysis": {},
        "hard_negative_analysis": {},
        "improvement_opportunities": [],
    }

    # --- Load failure dataset ---
    failures = []
    if FAILURE_FILE.exists():
        with open(FAILURE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    failures.append(json.loads(line))

    # --- Category frequency ---
    cat_counter = Counter()
    cat_by_question: dict[str, set] = defaultdict(set)
    for f in failures:
        cat = f.get("failure_category", "unclassified")
        cat_counter[cat] += 1
        cat_by_question[cat].add(f["question_id"])

    dashboard["failure_categories"] = {
        "frequency": dict(cat_counter.most_common()),
        "unique_questions_by_category": {cat: len(qids) for cat, qids in cat_by_question.items()},
        "total_failures": len(failures),
    }

    # --- Load mining data for query-level analysis ---
    mining_records = []
    if MINING_FILE.exists():
        with open(MINING_FILE, encoding="utf-8") as f:
            for line in f:
                line.strip() and mining_records.append(json.loads(line))

    # Query-level: questions with no gold in pool, gold ranked 11-20, etc.
    no_gold_in_pool = []
    gold_ranked_low = []
    for rec in mining_records:
        qid = rec["question_id"]
        positives = rec.get("positives", [])
        rec.get("pool_size", -1)

        if not positives:
            no_gold_in_pool.append(qid)
        elif all(p.get("rank", -1) > 10 for p in positives if p.get("rank", -1) > 0):
            gold_ranked_low.append(qid)

    dashboard["query_level_analysis"] = {
        "n_questions": len(mining_records),
        "gold_not_in_pool": {
            "count": len(no_gold_in_pool),
            "questions": no_gold_in_pool,
        },
        "gold_ranked_below_10": {
            "count": len(gold_ranked_low),
            "questions": gold_ranked_low[:20],  # limit for readability
        },
    }

    # --- Hard negative analysis ---
    neg_counter = Counter()
    neg_sections: Counter = Counter()
    neg_acts: Counter = Counter()
    tier_counts = Counter()

    for rec in mining_records:
        for neg in rec.get("negatives", []):
            tier_counts[neg.get("tier", 0)] += 1
            section = neg.get("section", "")
            act = neg.get("act_name", "")
            if section:
                neg_sections[section] += 1
            if act:
                neg_acts[act] += 1
            text_preview = neg.get("text", "")[:100]
            neg_counter[text_preview] += 1

    dashboard["hard_negative_analysis"] = {
        "tier_distribution": dict(tier_counts.most_common()),
        "top_sections": dict(neg_sections.most_common(20)),
        "top_acts": dict(neg_acts.most_common(10)),
        "unique_negative_previews": len(neg_counter),
    }

    # --- Improvement opportunities ---
    opportunities = []

    # Same-Act wrong-section is the biggest category
    same_act_count = cat_counter.get("A_same_act_wrong_section", 0)
    if same_act_count > 0:
        opportunities.append({
            "category": "A_same_act_wrong_section",
            "count": same_act_count,
            "impact": "HIGH",
            "recommendation": (
                "Hard-negative training on same-Act wrong-section pairs should "
                "directly improve these failures. The CE must learn to distinguish "
                "the correct section number within the same Act."
            ),
        })

    adj_count = cat_counter.get("K_adjacent_section_confusion", 0)
    if adj_count > 0:
        opportunities.append({
            "category": "K_adjacent_section_confusion",
            "count": adj_count,
            "impact": "HIGH",
            "recommendation": (
                "Adjacent-section confusion requires fine-grained section-number "
                "discrimination. Training on section-proximity-aware pairs (e.g. "
                "s16 vs s17 in the same Act) should help."
            ),
        })

    sub_count = cat_counter.get("B_same_section_wrong_subsection", 0)
    if sub_count > 0:
        opportunities.append({
            "category": "B_same_section_wrong_subsection",
            "count": sub_count,
            "impact": "MEDIUM",
            "recommendation": (
                "Subsection confusion is a fine-grained problem. Consider adding "
                "subsection-level identifiers to the payload and training pairs."
            ),
        })

    def_count = cat_counter.get("F_definition_vs_operative", 0)
    if def_count > 0:
        opportunities.append({
            "category": "F_definition_vs_operative",
            "count": def_count,
            "impact": "MEDIUM",
            "recommendation": (
                "The CE prefers definitions over operative provisions due to lexical "
                "overlap. Training on definition-vs-operative pairs should help."
            ),
        })

    dashboard["improvement_opportunities"] = sorted(
        opportunities, key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(x["impact"], 3)
    )

    return dashboard


def generate_report(dashboard: dict) -> str:
    """Generate a Markdown report from the dashboard data."""
    lines = [
        "# Error Analysis Dashboard — Hard-Negative Legal Reranking 1.0",
        "",
        f"**Total failures analyzed:** {dashboard['failure_categories']['total_failures']}",
        f"**Questions analyzed:** {dashboard['query_level_analysis']['n_questions']}",
        "",
        "---",
        "",
        "## 1. Failure Category Frequency",
        "",
        "| Category | Count | Unique Questions |",
        "|----------|------:|-----------------:|",
    ]

    freq = dashboard["failure_categories"]["frequency"]
    unique_q = dashboard["failure_categories"]["unique_questions_by_category"]
    for cat, count in sorted(freq.items(), key=lambda x: -x[1]):
        label = cat.replace("_", " ").title()
        uq = unique_q.get(cat, 0)
        lines.append(f"| {label} | {count} | {uq} |")

    lines.extend([
        "",
        "## 2. Query-Level Analysis",
        "",
        f"- **Gold not in pool:** {dashboard['query_level_analysis']['gold_not_in_pool']['count']} questions",
        f"- **Gold ranked below 10:** {dashboard['query_level_analysis']['gold_ranked_below_10']['count']} questions",
        "",
        "## 3. Hard Negative Distribution",
        "",
        "### By Tier",
        "",
    ])
    for tier, count in sorted(dashboard["hard_negative_analysis"]["tier_distribution"].items()):
        tier_label = {1: "Random", 2: "Semantic Hard", 3: "Adversarial Legal"}.get(tier, f"Tier {tier}")
        lines.append(f"- **Tier {tier} ({tier_label}):** {count} negatives")

    lines.extend([
        "",
        "### Top Sections in Negatives",
        "",
    ])
    for sec, count in list(dashboard["hard_negative_analysis"]["top_sections"].items())[:10]:
        lines.append(f"- Section {sec}: {count} occurrences")

    lines.extend([
        "",
        "## 4. Improvement Opportunities",
        "",
    ])
    for opp in dashboard["improvement_opportunities"]:
        lines.extend([
            f"### [{opp['impact']}] {opp['category'].replace('_', ' ').title()} ({opp['count']} failures)",
            "",
            opp["recommendation"],
            "",
        ])

    return "\n".join(lines)


def main() -> int:
    dashboard = build_dashboard()

    # Write JSON
    OUT_JSON.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write Markdown report
    report = generate_report(dashboard)
    OUT_MD.write_text(report, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
