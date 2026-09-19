# FBO Compliance Auditor Agent Blueprint

- **Status:** Proposed / Ready for Implementation
- **Target Seam:** `app/auditor/`
- **Related Modules:** `app/fbo_issue/`, `app/inspection/`, `app/rag/`, `app/ai_assistant/`
- **Domain Context:** [CONTEXT.md](file:///C:/github/NSA_webservice/CONTEXT.md)

---

## 1. Executive Summary & Persona

The **FBO Compliance Auditor Agent** acts as an **Independent Lead Food Safety & Regulatory Auditor (ISO 22000 / HACCP / FSSAI Master Auditor)**.

Its primary role is to bridge the gap between regulatory notices (e.g., FSO Inspection Checklists, Section 32 Improvement Notices, hygiene contraventions) and practical ground implementation. It consumes the FBO's operational context and identified shortcomings, searches the regulatory knowledge base using semantic search (RAG), and synthesizes a **feasible, cost-effective, and audit-proof Corrective and Preventive Action (CAPA) Workflow**.

---

## 2. System Architecture & Information Flow

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             FBO Context & Shortcomings                           │
│  (Inspection Checklist, FboIssue, Violation Details, Scale: Eatery/Manufacturer) │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   Step 1: Violation Ingestion & Risk Stratification             │
│  Classify findings: Critical (Food safety hazard) | Major (Statutory) | Minor     │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│              Step 2: Regulatory Search & Grounding (RAG / Knowledge Base)        │
│  Query FSSAI Schedule 4, SOP benchmarks, hygienic codes, statutory timeframes   │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│          Step 3: Feasibility & Resource Optimization (Auditor Logic)             │
│  Phased milestones: Immediate Containment (0-48h) ➔ Corrective ➔ Preventive     │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                                         ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│               Step 4: Output Structured Remediation Workflow Plan                │
│  - Root Cause Analysis (RCA)                                                     │
│  - Step-by-Step SOP Remediation Checklist                                        │
│  - Evidence/Dossier required for FSO Re-inspection (to close issue)             │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Data Schemas

### 3.1 Input Schema (`FBOAuditContext`)
```json
{
  "fbo_id": "FBO-MUM-2026-0812",
  "fbo_name": "Sunrise Bakers & Confectionery",
  "business_type": "Bakery / Food Manufacturer",
  "scale": "Small Enterprise",
  "inspection_date": "2026-09-18",
  "compliance_deadline_days": 15,
  "shortcomings": [
    {
      "item": "water_report",
      "observation": "Potable water test report from NABL lab not available on premises.",
      "severity": "major"
    },
    {
      "item": "pest_report",
      "observation": "Evidence of pest intrusion near flour storage; no pest control contract.",
      "severity": "critical"
    },
    {
      "item": "temperature_logging",
      "observation": "Cream storage refrigerator operating at 11°C (required <= 4°C).",
      "severity": "critical"
    }
  ]
}
```

### 3.2 Output Schema (`AuditorRemediationPlan`)
```json
{
  "plan_id": "CAPA-2026-0919-01",
  "fbo_id": "FBO-MUM-2026-0812",
  "executive_summary": "3 non-compliances identified (2 Critical, 1 Major). Total estimated compliance window: 12 days.",
  "root_cause_analysis": [
    {
      "shortcoming": "Pest intrusion & lack of records",
      "probable_root_cause": "Unsealed entry points in raw material storage; lack of scheduled vendor contract."
    }
  ],
  "remediation_phases": [
    {
      "phase": "Phase 1: Immediate Containment (Days 0-2)",
      "actions": [
        {
          "step_number": 1,
          "task": "Isolate contaminated ingredient bags and calibrate dairy refrigeration unit to 3°C.",
          "responsible_role": "Kitchen Supervisor",
          "estimated_cost_inr": 500,
          "regulatory_ref": "Schedule 4, Part II, Sec 2.1"
        }
      ]
    },
    {
      "phase": "Phase 2: Corrective Implementation (Days 3-8)",
      "actions": [
        {
          "step_number": 2,
          "task": "Engage FSSAI-approved pest control operator; seal door thresholds and drain meshes.",
          "responsible_role": "Facility Manager",
          "estimated_cost_inr": 4500,
          "regulatory_ref": "Schedule 4, Part II, Sec 6.2"
        },
        {
          "step_number": 3,
          "task": "Draw water sample from main supply and send to NABL-accredited lab for IS 10500 testing.",
          "responsible_role": "Quality Executive",
          "estimated_cost_inr": 2500,
          "regulatory_ref": "FSS Act §32 / Schedule 4, Sec 3.1"
        }
      ]
    },
    {
      "phase": "Phase 3: Preventive Standardization (Days 9-14)",
      "actions": [
        {
          "step_number": 4,
          "task": "Implement daily two-shift refrigeration temperature logging sheet.",
          "responsible_role": "Store In-charge",
          "estimated_cost_inr": 100,
          "regulatory_ref": "HACCP Principle 3 (Monitoring)"
        }
      ]
    }
  ],
  "dossier_checklist_for_fso": [
    "Copy of NABL water test report (IS 10500)",
    "Pest control service certificate and bait station map",
    "7-day temperature log chart for dairy storage",
    "Photographic evidence of mesh installation on external drains"
  ]
}
```

---

## 4. Implementation Specification

### 4.1 Module File Structure
```
app/auditor/
├── __init__.py           # Blueprint and module exports
├── models.py             # Pydantic / SQLAlchemy models for CAPA plans
├── prompt_templates.py   # Auditor system prompts & few-shot guidelines
├── search_adapter.py     # RAG bridge to query regulations/SOPs
├── service.py            # AuditorAgent orchestration engine
└── routes.py             # REST API endpoints for FBO/FSO workflows
```

### 4.2 Service Layer (`app/auditor/service.py`)
```python
"""app/auditor/service.py - FBO Compliance Auditor Agent Service."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai_assistant.service import AIAssistantService

logger = logging.getLogger(__name__)

AUDITOR_SYSTEM_PROMPT = """You are a Lead Food Safety Auditor (ISO 22000 / FSSAI Master Auditor).
Your task is to review FBO shortcomings and formulate a feasible, pragmatic, and audit-proof
Corrective and Preventive Action (CAPA) workflow.

Guiding Principles:
1. Feasibility & Prioritization: Group actions into Immediate (0-48h), Corrective (3-8d), and Preventive (9-14d).
2. Resource-Aware: Tailor recommendations to the FBO's business scale (keep operational costs realistic).
3. Statutory Grounding: Anchor all corrective requirements in FSSAI Schedule 4 regulations.
4. Evidentiary Rigour: Specify exact physical documents/photos needed to prove compliance during FSO re-inspection.

Always respond in strictly valid JSON matching the AuditorRemediationPlan schema.
"""


class FBOAuditorAgent:
    """Auditor agent creating feasible remediation workflows for FBOs."""

    def __init__(self, ai_service: AIAssistantService | None = None) -> None:
        self.ai = ai_service or AIAssistantService()

    def generate_remediation_plan(
        self,
        fbo_context: dict[str, Any],
        regulatory_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Synthesizes shortcomings and grounded regulations into an auditor-grade workflow."""
        shortcomings = fbo_context.get("shortcomings", [])
        if not shortcomings:
            return {"error": "No shortcomings provided for audit plan generation."}

        # Build retrieval context string
        evidence_text = ""
        if regulatory_evidence:
            evidence_text = "\n\nRelevant Regulatory Standards:\n" + "\n".join(
                f"- {item.get('citation', '')}: {item.get('text', '')}"
                for item in regulatory_evidence
            )

        user_prompt = f"""
FBO Profile:
- Business: {fbo_context.get('fbo_name', 'FBO')} ({fbo_context.get('business_type', 'General')})
- Scale: {fbo_context.get('scale', 'Small/Medium')}
- Compliance Window: {fbo_context.get('compliance_deadline_days', 15)} days

Observed Shortcomings:
{json.dumps(shortcomings, indent=2)}
{evidence_text}

Generate a comprehensive, structured CAPA remediation workflow in JSON format.
"""

        raw_response = self.ai.complete(
            system_prompt=AUDITOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.2,  # Low temperature for audit rigor
        )

        try:
            plan = json.loads(raw_response)
            return plan
        except json.JSONDecodeError:
            logger.error("Auditor agent emitted invalid JSON response: %s", raw_response)
            return {
                "raw_plan": raw_response,
                "error": "Failed to parse structured audit plan.",
            }
```

### 4.3 REST API Layer (`app/auditor/routes.py`)
```python
"""app/auditor/routes.py - API routes for FBO Auditor Agent."""

from flask import Blueprint, jsonify, request
from app.auditor.service import FBOAuditorAgent
from app.rag.retrieval.qdrant_client import search_regulations

auditor_bp = Blueprint("auditor", __name__, url_prefix="/api/v2/auditor")
auditor_agent = FBOAuditorAgent()


@auditor_bp.route("/generate-plan", methods=["POST"])
def generate_audit_plan():
    """Generates an auditor remediation plan from an inspection finding or FBO issue."""
    data = request.get_json() or {}
    shortcomings = data.get("shortcomings", [])

    if not shortcomings:
        return jsonify({"error": "Missing shortcomings list"}), 400

    # 1. Semantic search for regulatory standards relevant to the shortcomings
    search_queries = [s.get("item", "") for s in shortcomings if "item" in s]
    evidence = []
    if search_queries:
        retrieved = search_regulations(" ".join(search_queries), top_k=3)
        evidence = [{"citation": r.title, "text": r.snippet} for r in retrieved]

    # 2. Run Auditor Agent
    plan = auditor_agent.generate_remediation_plan(
        fbo_context=data,
        regulatory_evidence=evidence,
    )

    return jsonify({"status": "success", "plan": plan}), 200
```

---

## 5. Integration with Existing Workflows

1. **Inspection Checklist Hook**: In `app/inspection/`, when an FSO completes a 12-point inspection checklist and flags violations, a button *"Generate FBO Remediation Workflow"* will trigger this agent.
2. **Improvement Notice Attachment**: The generated remediation plan can be attached directly to the Section 32 Improvement Notice, giving the FBO clear instructions alongside the statutory warning.
3. **Closing Open Issues**: When the FBO submits the items listed in `dossier_checklist_for_fso`, the system can transition the issue to `Corrective Measures Implemented`.
