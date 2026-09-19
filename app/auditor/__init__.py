"""FBO Compliance Auditor Agent (FBO_AUDITOR_AGENT_BLUEPRINT.md).

Independent lead-auditor persona bridging regulatory notices and ground
implementation: stratifies checklist violations, grounds them via RAG, and
synthesizes a phased CAPA workflow with a verification dossier.

Pure seams (no app / LLM / network): :mod:`app.auditor.severity`,
:mod:`app.auditor.context`. LLM shell: :mod:`app.auditor.service`.
"""

from __future__ import annotations

from flask import Blueprint

auditor_bp = Blueprint("auditor", __name__, template_folder="templates")

from app.auditor import routes  # noqa: F401

__all__ = ["auditor_bp"]
