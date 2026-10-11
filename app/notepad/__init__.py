"""Notepad blueprint — Notes intake queue with AI evaluation and the
Legal-RAG Q&A thread.

See ``docs/NOTEPAD_LEGAL_RAG_PLAN.md`` and ``docs/NOTEPAD_LEGAL_RAG_SPEC.md``.
"""

from app.notepad.routes import notepad_bp

__all__ = ["notepad_bp"]
