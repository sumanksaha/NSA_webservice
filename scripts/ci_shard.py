"""Deterministic test-file sharder for CI.

The fast (non-slow) suite is large enough that a single pytest job both takes
~10-16 minutes and accumulates enough memory to get the runner killed
(``The runner has received a shutdown signal``).  ``pytest-xdist`` is not an
option here: ``tests/conftest.py`` pins one SQLite database and drops/recreates
it per session, so worker processes would race on the same file.  Splitting
into independent jobs keeps every shard short and small.

The split is round-robin over the sorted test-file list, so alphabetically
adjacent files (which are often related and similarly heavy) land in different
shards.  Given the same tree every shard sees the same set of files.

Usage (the workflow expands the output into argv)::

    python scripts/ci_shard.py --shard 1/3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Directories pytest is pointed at in the fast-test job.
ROOTS = ("legal_paragraph_detection_engine/tests", "tests")


def discover_tests() -> list[str]:
    """Every collected test file, as forward-slash paths, sorted."""
    found: list[str] = []
    for root in ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        found.extend(str(path.as_posix()) for path in root_path.rglob("test_*.py"))
    return sorted(found)


def shard_of(paths: list[str], index: int, total: int) -> list[str]:
    """Round-robin slice ``index`` (1-based) of ``total``."""
    return [path for position, path in enumerate(paths) if position % total == index - 1]


def _parse_shard(value: str) -> tuple[int, int]:
    try:
        index_text, total_text = value.split("/", 1)
        index, total = int(index_text), int(total_text)
    except ValueError as exc:  # pragma: no cover - argparse surfaces the message
        raise argparse.ArgumentTypeError(f"expected '<index>/<total>', got {value!r}") from exc
    if not 1 <= index <= total:
        raise argparse.ArgumentTypeError(f"shard index {index} outside 1..{total}")
    return index, total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shard", type=_parse_shard, default=_parse_shard("1/1"), help="shard as <index>/<total>")
    args = parser.parse_args(argv)

    index, total = args.shard
    paths = discover_tests()
    if not paths:
        print("ci_shard: no test files discovered", file=sys.stderr)
        return 1

    selected = shard_of(paths, index, total)
    if not selected:  # pragma: no cover - would mean more shards than files
        print(f"ci_shard: shard {index}/{total} is empty ({len(paths)} files)", file=sys.stderr)
        return 1

    print(" ".join(selected))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
