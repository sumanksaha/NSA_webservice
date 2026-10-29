#!/usr/bin/env python3 -S
"""Discover and repair 'dir-as-file' filesystem artifacts.

A dir-as-file artifact is when git porcelain reports a path whose name is a
file (e.g. evaluation/build_label_baseline.py) but on disk there is a
directory with that same name containing sub-files.  This tool:

  1. Detects every such artifact anywhere under the repo.
  2. For each, prints a repo-relative bundle path (the same path but with the
     .py stripped so it becomes a real directory).
  3. Prints the real on-disk children it would bundle into that directory.
  4. Prints a strict bash snippet that, *only when run by an operator*, moves
     those on-disk children into the bundle directory and touches (without
     staging) the git index entry so git porcelain no longer reports a broken
     file where a directory should be.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path.cwd()
REPO_ROOT_FRONTEND = Path(".").resolve()


def porcelain_status_map() -> dict[str, str]:
    # canonical porcelain path -> status (XY) from `git status --porcelain`
    out: dict[str, str] = {}
    try:
        raw = os.popen("git status --porcelain -- -uall", "r").read()
    except OSError as exc:
        print(f"[warn] git status: {exc}", file=sys.stderr)
        return out
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        status = line[0:2].replace(" ", ".")
        path = line[3:]
        # Normalise to forward slash for cross-platform comparison
        path_norm = path.replace("\\", "/")
        out[path_norm] = status
    return out


def disk_children_of_dirlike(porcelain_path: str) -> list[str]:
    """Return sorted repo-relative paths of disk files that live under a
    directory whose *name* equals the porcelain path's trailing name.

    Detection: if (REPO_ROOT / porcelain_path) exists on disk as a directory,
    then the affected bundle is REPO_ROOT/dirname(porcelain_path)/stem
    and we read all repo-relative files under it.
    """
    p = Path(porcelain_path.replace("\\", "/"))
    # porcelain_path is repo-relative, e.g. evaluation/build_label_baseline.py
    # construct the on-disk path under REPO_ROOT
    on_disk = (REPO_ROOT / p).resolve()
    # On a healthy FS, (REPO_ROOT / porcelain_path) is a file and is_file()
    # returns True.  If it's a directory we have the artifact.
    if not on_disk.is_dir():
        return []
    # gather children recursively
    results: list[str] = []
    for child in on_disk.rglob("*"):
        if not child.is_file():
            continue
        results.append(str(child.relative_to(REPO_ROOT).as_posix()))
    return sorted(results)


def repo_root_relative(path: str) -> str:
    """Return the repo-relative form of an absolute path, or the input if
    not under REPO_ROOT.
    """
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT.resolve()).as_posix())
    except ValueError:
        return path


def detect() -> dict[str, list[str]]:
    """Return {porcelain-path : [repo-relative disk children]}."""
    out: dict[str, list[str]] = defaultdict(list)
    for porcelain, _status in porcelain_status_map().items():
        if children := disk_children_of_dirlike(porcelain):
            out[porcelain] = children
    return out


def suggested_bundle_dir(porcelain_path: str) -> str:
    p = Path(porcelain_path.replace("\\", "/"))
    # turn foo.py into foo/
    if p.suffix == ".py":
        bundle = p.with_suffix("")
    else:
        bundle = p
    return str(bundle.relative_to(REPO_ROOT).as_posix())


def main() -> int:
    artifacts = detect()
    if not artifacts:
        print("No dir-as-file artifacts detected.", file=sys.stderr)
        return 0
    print("Dir-as-file artifacts detected.\n")
    for porcelain, children in sorted(artifacts.items()):
        bundle = suggested_bundle_dir(porcelain)
        print(f"\nArtifact: {porcelain}")
        print(f"  bundle dir: {bundle}")
        print(f"  on-disk children ({len(children)}):")
        for c in children:
            print(f"    {c}")
        print("  bash plan (copy-paste to apply, NOT executed):")
        if children:
            # mkdir -p target dir
            print(f"  mkdir -p {REPO_ROOT / bundle}")
            for child in children:
                # dest = bundle_dir / child_without_leading_slash
                dest = (Path(bundle) / child).as_posix()
                src = (REPO_ROOT / child).as_posix()
                print(f"  test -f {src!r} && mkdir -p {Path(dest).parent.as_posix()!r} && mv -f {src!r} {dest!r}")
        else:
            print("  on-disk children: (none)")
        print("  git plan (copy-paste to apply, NOT executed):")
        cur = porcelain.replace("\\", "/")
        print(f"  git update-index --add -- {cur}")
    print(f"\nTotal artifacts: {len(artifacts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
