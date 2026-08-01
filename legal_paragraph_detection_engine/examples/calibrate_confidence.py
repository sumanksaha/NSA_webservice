"""Confidence-calibration harness for the Legal Paragraph Detection Engine (T-29).

Runs the engine over a set of representative legal documents and reports the
distribution of the calibrated confidence components by paragraph type. This
lets you verify the F-13 fix: top-level structural paragraphs no longer score
0.0 on ``structure_detection``, and the overall blend is meaningful.

Usage::

    python legal_paragraph_detection_engine/examples/calibrate_confidence.py

Note:
    The engine is not yet pip-installable (T-42 open), so this script adds the
    repository root to ``sys.path`` to make the package importable when run
    directly.
"""

import sys
from collections import defaultdict
from pathlib import Path

# Allow running directly from the repo without the engine being installed.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from legal_paragraph_detection_engine import LegalParagraphEngine

# Representative documents: hierarchical acts, plain prose, citation-heavy text
DOCUMENTS: list[str] = [
    """\
    Section 3

    3(1) In addition to the provisions of this Act, the following shall apply:

    3(1)(a) For the purposes of this section, "concerned food" shall mean any food
    3(1)(b) Any person who violates this provision shall be liable for penalties

    Explanation:

    The above provisions are meant to ensure compliance with food safety standards.

    Note: This is a sample legal document for demonstration purposes.

    Schedule I

    Table 1: Classification of Food Items
    """,
    """\
    The Food Safety Act, 2020

    Section 5(1)

    5(1)(a) Registration shall be mandatory for all food businesses.
    5(1)(b) Registration applications shall be submitted to the Registrar.

    Provided that existing businesses may register within six months.

    Schedule II: Registration Procedures
    """,
    """\
    In matters pertaining to the licensing of food business operators, the
    authority shall consider the provisions of the Act (2020 SC 123/456) and
    the applicable regulations (HC 45/2021) before granting approval. The
    Registrar shall maintain records of all such approvals in accordance with
    Section 14 of the Act.
    """,
]

COMPONENTS = ("structure_detection", "content_quality", "citation_presence", "overall")


def main() -> None:
    """Process the sample documents and print a calibration summary."""
    engine = LegalParagraphEngine()

    by_type: dict[str, list[dict[str, float]]] = defaultdict(list)
    for doc in DOCUMENTS:
        for para in engine.process_document(doc):
            by_type[para["paragraph_type"]].append(para["confidence_scores"])

    print("Calibrated confidence scores by paragraph type (T-29)\n")
    header = f"{'paragraph_type':<12}" + "".join(f"{c[:14]:>16}" for c in COMPONENTS)
    print(header)
    print("-" * len(header))

    for para_type in sorted(by_type):
        rows = by_type[para_type]
        means = {c: sum(r[c] for r in rows) / len(rows) for c in COMPONENTS}
        row = f"{para_type:<12}"
        row += "".join(f"{means[c]:>16.3f}" for c in COMPONENTS)
        row += f"  (n={len(rows)})"
        print(row)

    # Sanity checks for the F-13 fix
    all_scores = [s for rows in by_type.values() for r in rows for s in r.values()]
    assert all(0.0 <= s <= 1.0 for s in all_scores), "score outside [0, 1]"
    structural = [
        s
        for para_type, rows in by_type.items()
        if para_type != "normal"
        for r in rows
        for s in [r["structure_detection"]]
    ]
    if structural:
        assert min(structural) > 0.0, "a structural paragraph scored 0.0 structure_detection"
        print(f"\n[OK] All {len(all_scores)} scores within [0, 1]")
        print(f"[OK] Structural paragraphs min structure_detection = {min(structural):.3f} (> 0)")
    else:
        print("\nNote: no structural paragraphs found in sample docs")


if __name__ == "__main__":
    main()
