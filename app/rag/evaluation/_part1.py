# Build workflow_evaluator.py piece by piece

path = r"C:/github/NSA_webservice/app/rag/evaluation/workflow_evaluator.py"

parts = []

# Part 1: Header and imports
parts.append('"""Workflow answer evaluator -- deterministic quality assessment for FSSAI workflow answers."""')
parts.append("")
parts.append("from __future__ import annotations")
parts.append("")
parts.append("import re")
parts.append("from dataclasses import dataclass")
parts.append("from typing import Any")
parts.append("")
parts.append("from app.rag.evaluation.metrics import EvalScore")
parts.append("from app.rag.evaluation.textmatch import content_tokens, token_coverage")
parts.append("from app.rag.verification.claim_extractor import ClaimExtractor")
parts.append("from app.rag.verification.evidence_verifier import EvidenceVerifier")
parts.append("")
parts.append(
    "__all__ = ['WorkflowAnswerEvaluation', 'evaluate_workflow_answer', 'score_faithfulness', 'score_completeness', 'score_citation_quality', 'score_structure']",
)
parts.append("")
parts.append('_CITATION_RE = re.compile(r"\\[(\\d+)\\]")')
parts.append('_FORM_RE = re.compile(r"\\\\b(form|forms?)\\\\s+(ii|iii|iv|v|vi|vii|viii)\\\\b", re.IGNORECASE)')

with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(parts))
