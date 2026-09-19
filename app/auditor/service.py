"""FBO Compliance Auditor Agent service (FBO_AUDITOR_AGENT_BLUEPRINT.md §4.2).

Persona: Independent Lead Food Safety & Regulatory Auditor. Consumes the
``FBOAuditContext`` (see :mod:`app.auditor.context`), grounds it with
regulatory evidence, and synthesizes a phased CAPA workflow via the shared
LLM surface (:meth:`AIAssistantService.complete_json`).

Fail-closed: empty shortcomings → error dict; unparseable LLM → error dict
(the raw prose is never rendered as fact); kill-switch off or unconfigured
LLM → ``RuntimeError`` (routes map to 503).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

AUDITOR_SYSTEM_PROMPT = """You are a Lead Food Safety Auditor (ISO 22000 / FSSAI Master Auditor).
Your task is to review FBO shortcomings and formulate a feasible, pragmatic, and audit-proof
Corrective and Preventive Action (CAPA) workflow.

Guiding Principles:
1. Feasibility & Prioritization: Group actions into Immediate (0-48h), Corrective (3-8d), and Preventive (9-14d).
2. Resource-Aware: Tailor recommendations to the FBO's business scale (keep operational costs realistic).
3. Statutory Grounding: Anchor all corrective requirements in FSSAI Schedule 4 regulations.
4. Evidentiary Rigour: Specify exact physical documents/photos needed to prove compliance during FSO re-inspection.

Always respond in strictly valid JSON matching the AuditorRemediationPlan schema
(plan_id, executive_summary, root_cause_analysis, remediation_phases, dossier_checklist_for_fso).
"""


class FBOAuditorAgent:
    """Auditor agent creating feasible remediation workflows for FBOs."""

    def __init__(self, ai_service: Any | None = None) -> None:
        self.ai = ai_service

    def _llm(self) -> Any:
        """Resolve the LLM surface (injected stub in tests, lazy service in requests)."""
        if self.ai is not None:
            return self.ai
        from app.ai_assistant.service import AIAssistantService

        return AIAssistantService()

    def generate_remediation_plan(
        self,
        fbo_context: dict[str, Any],
        regulatory_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Synthesize shortcomings and grounded regulations into a CAPA workflow."""
        from app.auditor.severity import stratify
        from app.shared.config import cfg

        if not bool(cfg.auditor_enabled):
            raise RuntimeError("Auditor AI is disabled (AUDITOR_AI_ENABLED=false).")

        shortcomings = (fbo_context or {}).get("shortcomings", [])
        if not shortcomings:
            return {"error": "No shortcomings provided for audit plan generation."}

        llm = self._llm()
        if not llm.is_enabled():
            raise RuntimeError("Auditor AI is not configured (missing API key or provider).")

        counts = stratify(shortcomings)
        evidence_lines = "\n".join(
            f"- {item.get('citation', '')}: {item.get('text', '')}"
            for item in (regulatory_evidence or [])
            if isinstance(item, dict)
        )
        evidence_text = f"\n\nRelevant Regulatory Standards:\n{evidence_lines}" if evidence_lines else ""
        user_prompt = f"""FBO Profile:
- Business: {fbo_context.get("fbo_name", "FBO")} ({fbo_context.get("business_type", "General")})
- Scale: {fbo_context.get("scale", "Standard FBO")}
- Compliance Window: {fbo_context.get("compliance_deadline_days", 15)} days
- Findings: {counts["critical"]} Critical, {counts["major"]} Major, {counts["minor"]} Minor

Observed Shortcomings:
{json.dumps(shortcomings, indent=2)}
{evidence_text}

Generate a comprehensive, structured CAPA remediation workflow in JSON format."""

        try:
            plan: dict[str, Any] = dict(llm.complete_json(AUDITOR_SYSTEM_PROMPT, user_prompt, temperature=0.2))
        except ValueError as exc:
            logger.error("Auditor agent emitted invalid JSON: %s", exc)
            return {"error": "Failed to parse structured audit plan.", "detail": str(exc)}

        # Shape guarantees so templates never break on a sparse plan.
        plan.setdefault("remediation_phases", [])
        plan.setdefault("dossier_checklist_for_fso", [])
        return plan
