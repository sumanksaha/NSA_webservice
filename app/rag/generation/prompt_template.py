"""Prompt templates for grounded RAG generation.

Provides a template registry and rendering for the grounded-QA prompt
used by the LLM client to produce citable legal answers.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

GROUND_QA_SYSTEM_PROMPT = (
    "You are a legal assistant specialised in the Food Safety and Standards "
    "Act, 2006 (FSS Act). Answer questions using ONLY the provided context. "
    "Cite sources using [n] markers where n is the source number shown in the "
    "context (e.g. [1], [2]). If the answer is not in the context, state so "
    "clearly. Never fabricate facts or cite sources not in the context. "
    "Keep answers concise and legally precise."
)

GROUND_QA_USER_TEMPLATE = (
    "Question: {query}\n\n"
    "Retrieved legal context:\n"
    "{context}\n\n"
    "Answer the question using the context above. "
    "Cite specific sources with [n] markers.\n"
    "Answer:"
)

_TEMPLATES: dict[str, tuple[str, str]] = {
    "grounded_qa": (GROUND_QA_SYSTEM_PROMPT, GROUND_QA_USER_TEMPLATE),
}


class PromptTemplate:
    """Render grounded-QA prompts from a template registry."""

    def __init__(self, templates: dict[str, tuple[str, str]] | None = None) -> None:
        self._templates: dict[str, tuple[str, str]] = (
            dict(templates) if templates else dict(_TEMPLATES)
        )

    @property
    def available_actions(self) -> list[str]:
        return list(self._templates.keys())

    def render(
        self,
        action: str,
        *,
        query: str,
        context: str,
        extra_vars: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Render a named prompt template.

        Returns:
            A ``(system_prompt, user_prompt)`` tuple.
        """
        if action not in self._templates:
            raise ValueError(
                f"Unknown prompt action: {action!r}. "
                f"Available: {list(self._templates.keys())}"
            )
        system_prompt, user_template = self._templates[action]
        vars_dict: dict[str, Any] = {"query": query, "context": context}
        if extra_vars:
            vars_dict.update(extra_vars)
        user_prompt = user_template.format(**vars_dict)
        return system_prompt, user_prompt

    def render_default(self, query: str, context: str, **extra_vars: Any) -> tuple[str, str]:
        """Convenience: render the ``grounded_qa`` template."""
        return self.render(
            "grounded_qa",
            query=query,
            context=context,
            extra_vars=extra_vars or None,
        )
