"""Pin the ``open()`` mode-parsing in ``scripts/check_import_side_effects.py``.

That function is the guard against the defect class that repeatedly rewrote
``app/rag/evaluation/workflow_evaluator.py``, so a false negative in it is
worse than no checker at all: it reports "all clean" while the landmine is
still armed. Two real false negatives are pinned here, both found by review
rather than by a failing repo:

* ``open("x.py", "w")`` returned False — a literal path in write mode was
  missed, hiding five further offenders in ``evaluation/_gen_*.py``;
* ``open(file=p)`` raised ``UnboundLocalError``, which aborted the entire
  check rather than reporting one file.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_import_side_effects import _is_write_mode, _scan_module_level_writes  # noqa: E402


def _mode(src: str) -> bool:
    return _is_write_mode(ast.parse(src).body[0].value)


class TestIsWriteMode:
    def test_positional_write_and_append(self):
        assert _mode('open(p, "w")')
        assert _mode('open("x.py", "w")')
        assert _mode('open("x.py", "a")')

    def test_read_and_default_are_not_writes(self):
        assert not _mode('open(p)')
        assert not _mode('open("x.py", "r")')
        assert not _mode('open("x.py", encoding="utf-8")')

    def test_keyword_only_call_does_not_raise(self):
        """Regression: ``mode`` was unbound when there were no positional args."""
        assert not _mode("open(file=p)")
        assert _mode('open(file="x.py", mode="w")')


class TestModuleScopeWrites:
    def _scan(self, src: str) -> list[int]:
        return _scan_module_level_writes(ast.parse(src))

    def test_module_scope_write_is_flagged(self):
        assert self._scan('open("x.py", "w")\n')

    def test_guarded_write_is_not_flagged(self):
        assert not self._scan('if __name__ == "__main__":\n    open("x.py", "w")\n')

    def test_write_inside_a_function_is_not_flagged(self):
        assert not self._scan('def f():\n    open("x.py", "w")\n')

    def test_non_python_target_is_ignored(self):
        assert not self._scan('open("out.json", "w")\n')
