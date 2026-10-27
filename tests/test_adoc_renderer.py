"""Tests for the shared ``.adoc`` → DOCX renderer (``app/shared/adoc_renderer.py``).

Two failure modes are covered here, both of which are invisible without pandoc:

1. A malformed AsciiDoc template — an unterminated ``////`` comment block —
   makes pandoc's asciidoc reader spin forever while allocating memory. That
   is what killed the CI runner (exit 143, no test output), so template hygiene
   is asserted structurally rather than by rendering.
2. A converter that never terminates must degrade to the python-docx fallback
   instead of wedging the calling request.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.shared import adoc_renderer

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_ROOTS = (
    _REPO_ROOT / "app" / "case_file_generator" / "templates",
    _REPO_ROOT / "app" / "templates",
)

# A comment block is fenced by a line that is exactly "////".
_FENCE_RE = re.compile(r"^////\s*$")


def _adoc_templates() -> list[Path]:
    templates: list[Path] = []
    for root in _TEMPLATE_ROOTS:
        if root.is_dir():
            templates.extend(sorted(root.rglob("*.adoc")))
    return templates


class TestTemplateHygiene:
    """Guards the input to pandoc: an unbalanced fence is an infinite loop."""

    def test_templates_are_discovered(self):
        assert _adoc_templates(), "expected .adoc templates under the app template roots"

    def test_comment_fences_are_balanced(self):
        offenders = []
        for path in _adoc_templates():
            fences = [
                i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if _FENCE_RE.match(line)
            ]
            if len(fences) % 2:
                offenders.append(f"{path.relative_to(_REPO_ROOT)} (unclosed fence at line {fences[-1]})")
        assert not offenders, "unbalanced AsciiDoc comment fences make pandoc hang: " + ", ".join(offenders)


class TestPandocTimeout:
    """A hung converter must fall back, not block."""

    def test_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(adoc_renderer, "PANDOC_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(
            adoc_renderer,
            "_pandoc_cmd",
            lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"],
        )

        assert adoc_renderer._pandoc_asciidoc_to_docx("Title\n") is None

    def test_missing_binary_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        def _raise(*args: str) -> list[str]:
            raise FileNotFoundError("pandoc binary not found on PATH")

        monkeypatch.setattr(adoc_renderer, "_pandoc_cmd", _raise)

        assert adoc_renderer._pandoc_asciidoc_to_docx("Title\n") is None

    def test_success_returns_stdout_bytes(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            adoc_renderer,
            "_pandoc_cmd",
            lambda *args: [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'PK\x03\x04docx')"],
        )

        assert adoc_renderer._pandoc_asciidoc_to_docx("Title\n") == b"PK\x03\x04docx"

    def test_nonzero_exit_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            adoc_renderer,
            "_pandoc_cmd",
            lambda *args: [sys.executable, "-c", "raise SystemExit(2)"],
        )

        assert adoc_renderer._pandoc_asciidoc_to_docx("Title\n") is None
