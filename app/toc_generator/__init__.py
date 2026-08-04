"""Dynamic Table of Contents generator (Phase 7).

Exports the ``TocGeneratorEngine`` and ``TocEntry`` classes, plus the
``generate_toc_data`` convenience function for use in views.
"""

from app.toc_generator.engine import TocEntry, TocGeneratorEngine

__all__ = [
    "TocEntry",
    "TocGeneratorEngine",
]


def generate_toc_data(html: str) -> dict:
    """Convenience wrapper around ``TocGeneratorEngine.generate_toc_data``.

    Mirrors the ``generate_xref_report_data`` pattern from Phase 6.
    """
    return TocGeneratorEngine().generate_toc_data(html)
