"""Plugin Architecture package (Phase 20).

Provides a registry-based plugin system for swapping core service providers
at runtime via configuration:

- **OCR providers** — default: ``EasyOCRPlugin`` (wraps ``OCRPipeline``)
- **AI providers** — default: ``OpenRouterAIPlugin`` (wraps ``AIAssistantService``)
- **Rule providers** — default: ``FSSAIRuleSuggesterPlugin`` (wraps ``suggest_sections``)
- **PDF providers** — default: ``WeasyPrintPDFPlugin`` (wraps ``PDFAssemblyEngine``)

Plugins are registered at app-factory startup via :func:`register_default_plugins`,
which is called from ``create_app()``.  Each plugin wraps its underlying
implementation with a lazy import so ``import app.plugins`` never triggers
hard dependencies (torch, easyocr, httpx, weasyprint).

Usage::

    from app.plugins import PluginRegistry

    # Get the active OCR provider (reads OCR_PROVIDER config)
    ocr = PluginRegistry.get_instance().get_active("ocr")
    result = ocr.extract_text("/path/to/file.pdf")

    # Or get a specific named plugin
    rules = PluginRegistry.get_instance().get("rules", "fssai_default")
    sections = rules.suggest_sections(case_data)
"""

from __future__ import annotations

import logging

from app.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "PluginRegistry",
    "register_default_plugins",
]


def register_default_plugins() -> None:
    """Register all default plugin implementations into the singleton registry.

    Called from ``create_app()`` during app factory execution.  Each
    plugin class has already been imported via the lazy-import pattern
    (the plugin modules are imported here, but their backend implementations
    remain lazy).

    Safe to call multiple times — duplicate registrations are silently
    overwritten (same class).
    """
    from app.plugins.ai_plugins import OpenRouterAIPlugin
    from app.plugins.ocr_plugins import EasyOCRPlugin
    from app.plugins.pdf_plugins import WeasyPrintPDFPlugin
    from app.plugins.rule_plugins import FSSAIRuleSuggesterPlugin

    registry = PluginRegistry.get_instance()
    registry.register("ocr", "easyocr", EasyOCRPlugin)
    registry.register("ai", "openrouter", OpenRouterAIPlugin)
    registry.register("rules", "fssai_default", FSSAIRuleSuggesterPlugin)
    registry.register("pdf", "weasyprint", WeasyPrintPDFPlugin)

    logger.debug("Default plugins registered: ocr/easyocr, ai/openrouter, rules/fssai_default, pdf/weasyprint")
