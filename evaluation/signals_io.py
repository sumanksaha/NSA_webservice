"""Loading for the S1 production-signal log.

``s1_pipeline_signals.jsonl`` is append-only by design: the collector resumes
from the qids it has seen, and a forced re-run (``--only``) appends a second
row for the same question instead of rewriting history.  Analysis must
therefore treat the **last** row per ``question_id`` as authoritative —
otherwise a repaired row is silently double-counted next to its broken
predecessor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_signals(path: Path | str) -> list[dict[str, Any]]:
    """Read the append-only signal log, keeping the last row per question.

    Order follows first appearance, so downstream reports stay stable when a
    handful of rows are appended out of order.
    """
    rows: dict[str, dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        qid = record.get("question_id")
        if qid is None:
            continue
        rows[qid] = record
    return list(rows.values())
