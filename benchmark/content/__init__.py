"""Benchmark question content modules.

Each submodule exports ``QUESTIONS`` (a list of compact question tuples) and
``PROVISIONS`` (a list of gold provision records).  The assembler
(``../build_benchmark.py``) expands the compact tuples into the full frozen
schema and emits all v1.0 benchmark artifacts.
"""

from __future__ import annotations

QUESTIONS: list = []
PROVISIONS: list = []

for _name, _qn, _pn in [
    ("fssai", "QUESTIONS", "PROVISIONS"),
    ("animal", "QUESTIONS", "PROVISIONS"),
    ("env", "QUESTIONS", "PROVISIONS"),
    ("public_health", "QUESTIONS", "PROVISIONS"),
    ("wb", "QUESTIONS", "PROVISIONS"),
    ("commercial", "QUESTIONS", "PROVISIONS"),
    ("cross", "QUESTIONS", "PROVISIONS"),
]:
    try:
        _mod = __import__(f"benchmark.content.{_name}", fromlist=[_qn])
        QUESTIONS.extend(getattr(_mod, _qn))
        PROVISIONS.extend(getattr(_mod, _pn))
    except ModuleNotFoundError:
        # Module not yet authored during incremental development.
        pass
