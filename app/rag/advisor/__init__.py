"""FSO strategic advisory seam (ADR-0003, deterministic game-theory + Talebian).

Pure-Python Act selection over grounded statutory sections — no LLM, no
network, no DB. The graph adapter lives in
:mod:`app.rag.agent.nodes.advisory`; this package owns the math.
"""

from __future__ import annotations

from app.rag.advisor.ladder import ACTION_PROFILES, ActionProfile, EscalationLevel
from app.rag.advisor.penalties import FSSAI_PENALTY_SCHEDULE, StatutoryAnchor
from app.rag.advisor.selector import DeterministicActSelector, compute_fso_advisory

__all__ = [
    "ACTION_PROFILES",
    "FSSAI_PENALTY_SCHEDULE",
    "ActionProfile",
    "DeterministicActSelector",
    "EscalationLevel",
    "StatutoryAnchor",
    "compute_fso_advisory",
]
