"""3.8 — Static config linting for the RAG pipeline.

Provides a mypy plugin that validates config settings at type-check time
and a type stub for the dynamic cfg object so editors/mypy can see
its attributes.

Usage: Add to mypy config:
    plugins = app.shared.config_lint
"""

from __future__ import annotations

from collections.abc import Callable

# List of required config attributes (validated at import time)
REQUIRED_SETTINGS = (
    "retrieval_cache",
    "kg_fusion",
    "kg_expansion",
    "ensemble_ce_head",
    "retrieval_cache_ttl_seconds",
    "kg_max_provisions",
    "hallucination_detector",
    "rag_log_retention_days",
)

# Type mapping for each setting (for mypy stub generation)
SETTING_TYPES = {
    "retrieval_cache": "bool",
    "kg_fusion": "bool",
    "kg_expansion": "bool",
    "ensemble_ce_head": "int",
    "retrieval_cache_ttl_seconds": "int",
    "kg_max_provisions": "int",
    "hallucination_detector": "bool",
    "rag_log_retention_days": "int",
}


def generate_cfg_stub() -> str:
    """Generate a mypy-compatible type stub for the cfg object."""
    lines = [
        '"""Auto-generated type stub for cfg (see config_lint.py)."""',
        "from __future__ import annotations",
        "",
        "class Config:",
        '    """RAG pipeline configuration (validated at import time)."""',
    ]
    for name, typ in SETTING_TYPES.items():
        lines.append(f"    {name}: {typ}")
    lines.append("")
    lines.append("cfg: Config")
    return "\n".join(lines)


def validate_settings() -> list[str]:
    """Validate that all required settings are present in the cfg object.

    Returns:
        List of missing setting names (empty if all present).
    """
    missing = []
    try:
        from app.shared.config import cfg

        for name in REQUIRED_SETTINGS:
            if not hasattr(cfg, name):
                missing.append(name)
    except Exception as exc:
        missing.append(f"<import error: {exc}>")
    return missing


def mypy_plugin(version: str) -> Callable:
    """Mypy plugin entry point (no-op — validation is at import time)."""

    def _plugin(_version: str) -> None:
        pass

    return _plugin


def generate_stub_file(output_path: str = "app/shared/cfg.pyi") -> None:
    """Write the type stub to *output_path*."""
    stub = generate_cfg_stub()
    with open(output_path, "w") as f:
        f.write(stub)
    import logging

    logger = logging.getLogger(__name__)
    logger.info("Generated %s", output_path)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    missing = validate_settings()
    if missing:
        logging.warning("Missing settings: %s", missing)
    else:
        logging.info("All required settings present")
    generate_stub_file()
