"""Plugin registry — singleton store for provider plugins.

Follows the singleton pattern used elsewhere in the codebase (``_get_client``
in ``app/utils/storage.py``, ``_fso_sync_lock`` in ``app/__init__.py``).

Plugins are registered at app-factory startup via
:func:`app.plugins.register_default_plugins` and retrieved at call time
via :meth:`PluginRegistry.get_active`, which reads Flask config keys
(``{CATEGORY}_PROVIDER``) lazily — mirroring the
``current_app.config`` resolution pattern used by
``app/rag/retrieval/dense_retriever.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

#: Config key template — ``OCR_PROVIDER`` / ``AI_PROVIDER`` etc.
_CONFIG_KEY_TEMPLATE = "{category}_PROVIDER"

#: Default provider names per category (used when config/env var is unset).
_DEFAULT_ACTIVE: dict[str, str] = {
    "ocr": "easyocr",
    "ai": "openrouter",
    "rules": "fssai_default",
    "pdf": "weasyprint",
}


class PluginRegistry:
    """Singleton registry mapping (category, name) → provider class.

    Usage::

        registry = PluginRegistry.get_instance()
        registry.register("ocr", "easyocr", EasyOCRPlugin)
        plugin = registry.get_active("ocr")  # reads OCR_PROVIDER config

    The registry stores **classes**, not instances, and instantiates them
    lazily on ``get()`` so that lazy-import side-effects (e.g. torch model
    loading) only fire when the provider is actually called.
    """

    _instance: PluginRegistry | None = None
    # {category: {name: cls}}
    _plugins: ClassVar[dict[str, dict[str, type]]] = {}
    # {category: active_name}  — overrides config-derived defaults
    _active: ClassVar[dict[str, str]] = {}

    def __init__(self) -> None:
        # Private — use get_instance().
        pass

    # ------------------------------------------------------------------ #
    # Singleton access
    # ------------------------------------------------------------------ #

    @classmethod
    def get_instance(cls) -> PluginRegistry:
        """Return the singleton :class:`PluginRegistry` instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear all registrations and reset the singleton (for tests)."""
        cls._instance = None
        cls._plugins = {}
        cls._active = {}

    # ------------------------------------------------------------------ #
    # Registration & retrieval
    # ------------------------------------------------------------------ #

    def register(self, category: str, name: str, cls: type) -> None:
        """Register a plugin class under (category, name).

        Args:
            category: Provider category (``"ocr"``, ``"ai"``, ``"rules"``, ``"pdf"``).
            name: The plugin's unique name within the category.
            cls: The plugin class (must subclass the appropriate ``*Provider`` ABC).
        """
        if category not in self._plugins:
            self._plugins[category] = {}
        self._plugins[category][name] = cls
        logger.debug("Plugin registered: %s/%s → %s", category, name, cls.__name__)

    def get(self, category: str, name: str) -> Any:
        """Instantiate and return the plugin registered under (category, name).

        Raises:
            KeyError: If no plugin is registered under that name.
        """
        try:
            cls = self._plugins[category][name]
        except KeyError:
            raise KeyError(
                f"No plugin registered for {category}/{name}. Available: {self.available(category)}",
            ) from None
        return cls()

    def get_active(self, category: str) -> Any:
        """Return the active plugin for *category*.

        Resolution order:
          1. Explicitly-set active name (``self._active``)
          2. ``current_app.config["{CATEGORY}_PROVIDER"]``
          3. ``os.environ["{CATEGORY}_PROVIDER"]``
          4. Built-in default from ``_DEFAULT_ACTIVE``
        """
        active_name = self._active.get(category)

        if active_name is None:
            # Try Flask config (lazy — only works inside app context)
            config_key = _CONFIG_KEY_TEMPLATE.format(category=category.upper())
            try:
                from flask import current_app

                active_name = current_app.config.get(config_key)
            except (RuntimeError, ImportError):
                pass

            # Fall back to env var
            if not active_name:
                active_name = os.environ.get(config_key)

            # Fall back to built-in default
            if not active_name:
                active_name = _DEFAULT_ACTIVE.get(category, "")

        return self.get(category, active_name)

    def available(self, category: str) -> list[str]:
        """Return all registered plugin names for *category*."""
        return list(self._plugins.get(category, {}).keys())

    def set_active(self, category: str, name: str) -> None:
        """Override the active plugin name for *category* (test helper)."""
        self._active[category] = name

    def is_registered(self, category: str, name: str) -> bool:
        """Check whether a plugin is registered under (category, name)."""
        return name in self._plugins.get(category, {})


__all__ = ["PluginRegistry"]
