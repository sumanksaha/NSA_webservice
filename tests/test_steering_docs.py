"""Guard the navigation pointers that steering files hand to every agent.

`AGENTS.md` and `CLAUDE.md` are pushed into each agent's context. Their value is
entirely in the pointers: a pointer whose target is missing sends the agent into
a wall, and nothing else in the toolchain notices — there is no link checker in
CI or pre-commit.

Two extraction rules, chosen so neither produces false positives:

* **Backticked paths in the steering files** resolve from the repo root, which
  is how those pointers are written (``docs/agents/...``, ``CONTEXT.md``).
  A backtick token counts as a path only when it contains ``/`` or ends in
  ``.md``, so label strings like ``needs-triage`` are not mistaken for paths.
* **Markdown links in `docs/INDEX.md`** resolve relative to that file, which is
  how Markdown link targets work.

The test asserts *existence*, not content — content is the owning doc's job.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Always-loaded steering files: pointers resolve from the repo root.
STEERING_FILES = ("AGENTS.md", "CLAUDE.md")

#: The central index: link targets resolve relative to docs/.
DOC_INDEX = ROOT / "docs" / "INDEX.md"

_BACKTICK = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _looks_like_path(token: str) -> bool:
    token = token.strip()
    if not token or " " in token:
        return False
    if token.startswith(("-", "--", "http://", "https://", "#")):
        return False
    return "/" in token or token.endswith(".md")


def _backtick_paths(text: str) -> list[str]:
    return [t.strip() for t in _BACKTICK.findall(text) if _looks_like_path(t)]


def _link_targets(text: str) -> list[str]:
    return [t for t in _MD_LINK.findall(text) if not t.startswith(("http://", "https://", "#"))]


def test_steering_file_backtick_paths_exist() -> None:
    missing: list[str] = []
    found = 0
    for name in STEERING_FILES:
        path = ROOT / name
        assert path.exists(), f"{name} is missing — it is the always-loaded steering file"
        for target in _backtick_paths(path.read_text(encoding="utf-8")):
            found += 1
            if not (ROOT / target).exists():
                missing.append(f"{name} -> `{target}`")
    # The files must actually contain pointers; an extraction regression would
    # otherwise let this test pass vacuously.
    assert found >= 6, f"expected steering files to carry navigation pointers, extracted only {found}"
    assert not missing, "dead pointers in steering files:\n  " + "\n  ".join(missing)


def test_steering_file_markdown_links_exist() -> None:
    missing: list[str] = []
    for name in STEERING_FILES:
        path = ROOT / name
        for target in _link_targets(path.read_text(encoding="utf-8")):
            if not (ROOT / target).exists():
                missing.append(f"{name} -> {target}")
    assert not missing, "dead markdown links in steering files:\n  " + "\n  ".join(missing)


def test_doc_index_links_exist() -> None:
    assert DOC_INDEX.exists(), "docs/INDEX.md is the central index and must exist"
    missing: list[str] = []
    found = 0
    for target in _link_targets(DOC_INDEX.read_text(encoding="utf-8")):
        found += 1
        if not (DOC_INDEX.parent / target).exists():
            missing.append(target)
    assert found >= 10, f"expected INDEX.md to index the docs, extracted only {found} links"
    assert not missing, "dead links in docs/INDEX.md:\n  " + "\n  ".join(missing)


def test_index_is_reachable_from_steering_files() -> None:
    """The central index is useless if no always-loaded pointer reaches it."""
    reachable = any("docs/INDEX.md" in _backtick_paths((ROOT / n).read_text(encoding="utf-8")) for n in STEERING_FILES)
    assert reachable, "docs/INDEX.md is not referenced by AGENTS.md or CLAUDE.md — it is orphaned"
