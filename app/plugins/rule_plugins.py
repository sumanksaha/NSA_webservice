"""Plugin implementations for rule/suggestion providers.

Wraps :func:`app.utils.suggester.suggest_sections` behind the
``RuleProvider`` interface.

Uses lazy imports — ``suggest_sections`` is only imported when
:meth:`suggest_sections` is called.
"""

from __future__ import annotations

import logging
from typing import Any

from app.plugins.base import RuleProvider

logger = logging.getLogger(__name__)


class FSSAIRuleSuggesterPlugin(RuleProvider):
    """Rule provider wrapping the FSSAI section suggester.

    The underlying :func:`app.utils.suggester.suggest_sections` is a pure
    function (no side effects, no I/O) that recommends applicable FSS Act
    sections based on inspection checklist values and case flags.  It is
    imported lazily so that the plugin module is import-safe even when the
    suggester's data file (``fss_sections.md``) is missing at import time.
    """

    def suggest_sections(self, case_data: dict[str, Any]) -> dict[str, Any]:
        """Suggest FSS Act sections for a case.

        Args:
            case_data: Dict of case fields (checklist items, section flags,
                case characteristics like ``non_license``).

        Returns:
            Dict with ``sections`` (list[str]) and ``reasoning`` (dict) keys,
            matching the return type of :func:`suggest_sections`.
        """
        from app.utils.suggester import suggest_sections  # lazy

        result = suggest_sections(case_data)
        # Ensure consistent return shape
        if isinstance(result, dict):
            return {
                "sections": result.get("sections", []),
                "reasoning": result.get("reasoning", {}),
            }
        # Backwards compat: if suggest_sections ever returns a plain list
        return {"sections": result, "reasoning": {}}


__all__ = ["FSSAIRuleSuggesterPlugin"]
