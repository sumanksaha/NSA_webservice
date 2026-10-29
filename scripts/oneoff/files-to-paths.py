#!/usr/bin/env python3 -S
"""Repair the 'evaluation/' dir-as-file artifacts that the agent's earlier
write_sequence emitted for build_label_baseline.py, build_train_pool.py,
retrieval_answer_link.py, and train_legal_ce_v3.py.

Plan:
  1. Detect all 'evaluation/<name>.py' dir-as-file files on disk and in git.
  2. For each, split the contents into one bundle dir per basename
     (evaluation/build_label_baseline/..., evaluation/build_train_pool/...,
     evaluation/retrieval_answer_link/..., evaluation/train_legal_ce_v3/...),
     preserving leaf names exactly (tests/, doc/, nested sub-files keep their
     relative tree).
  3. Replace each dir-as-file path in git porcelain with the directory, so git
     no longer reports a file where a directory must be.  Do NOT stage or
     commit anything — print what would be staged and a ready-made
     `git update-index --add` / `git rm` sequence, only committing when asked.
  4. Emit a strict bash snippet that can be copy-pasted to force the damaged
     paths into a usable directory form on disk if the repo was left with
     unreadable/empty directories.
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path.cwd()
REPO_ROOT_GIT = Path(".git").resolve()
DISK_READABLE_TOP_LINELEN = 60


def git_porcelain_files() -> set[tuple[str, str]]:
    """Return {(path, status)} for files git believes exist, untouched by us."""
    try:
        out: str = os.popen(
            "git status --porcelain -uall",
            "r",
        ).read()
    except Exception as exc:  # pragma: no cover - unlikely in our env
        print(f"[warn] git status failed ({exc}); falling back to empty set", file=sys.stderr)
        return set()
    result: set[tuple[str, str]] = set()
    for line in out.splitlines():
        # git status porcelain columns:  XY PATH
        if not line.strip():
            continue
        status = line[:2].replace(" ", "?")
        path = line[3:]
        result.add((path, status))
    return result


def disk_dir_as_files(top: Path = ROOT) -> dict[str, list[Path]]:
    """Map each 'dir-as-file' path (a path git thinks is a file but disk has a dir)
    onto the real files that live underneath it on disk.

    A 'file' whose name also exists as a directory on disk is the signature here:
        evaluation/build_label_baseline.py          <- git porcelain (file)
        evaluation/build_label_baseline/           <- real disk (dir)   OR
        evaluation/build_label_baseline.py         <- real disk (dir, broken)
    """
    row: dict[str, list[Path]] = defaultdict(list)
    for p in top.rglob("*"):
        if not p.is_file() and not p.is_dir():
            continue
        # git will list the dir-as-file name without trailing slash when the
        # object on disk is a directory.  Build the candidate set.
        name = p.name
        parent = p.parent
        # Is the parent path among git porcelain paths that git thinks is a file?
        parent_str = str(parent)
        candidate = f"{parent_str}/{name}"
        # normalize: git porcelain paths use forward slashes on posix and
        # backslashes on windows; git itself reports forward slashes, but we
        # still canonicalise to forward so comparisons are stable.
        # Does disk have a path under candidate that is a directory OR is
        # candidate_norm itself a file we must avoid treating as 'the' dir-as-file
        # parent of a dir?  We only flag the case where git porcelain has the
        # name '<parent>/foo.py' and disk has '<parent>/foo/' (dir) AND/OR
        # '<parent>/foo.py' is itself a directory on disk.
        for cand in (candidate, candidate + os.sep):
            dpath = Path(cand)
            if dpath.is_dir():
                # '<parent>/foo.py/' is real disk dir, and git porcelain says
                # '<parent>/foo.py' is a file -> dir-as-file.
                row[str(parent / name.replace(".py", ""))].append(dpath)
                # also all children live under it
                break
    # Now expand: for each dir-as-file path (the git porcelain basename without
    # .py), gather all real disk files located under it.  But also handle the
    # case where disk has a file literally named 'foo.py' that is itself a dir
    # (broken fs write).  In that case the children appear as siblings inside the
    # (non-existent) parent path on disk?  No — on real fs a file can't have
    # children; so the only sane 'dir-as-file' case is that disk has a DIR named
    # the same as the porcelain file.  Detect that below.
    out: dict[str, list[Path]] = defaultdict(list)
    for git_path, _ in git_porcelain_files():
        gp = Path(git_path.replace("\\", "/"))
        if not gp.is_file() and not gp.is_dir():
            # git porcelain path not present on disk — can't detect; skip
            continue
        # Case A: git porcelain path itself is a dir on disk (broken).
        if gp.is_dir():
            out[str(gp)].extend(sorted(p for p in gp.rglob("*") if p.is_file()))
            continue
        # Case B: git porcelain path filename (without .py) exists as a dir on
        # disk; gather contents under that real dir.
        stem_dir = gp.parent / gp.stem  # drop .py -> parent/dirname
        if stem_dir.is_dir():
            out[str(gp)].extend(sorted(stem_dir.rglob("*")) if stem_dir.rglob("*") else [])
            # also include the dir itself? no — it's a dir, not a file; we'll
            # represent the gap by listing its children
    # Fallback: also gather any 'foo.py' path that git porcelain shows as a file
    # but that, on disk, is a directory (broken FS write).  We detect by asking
    # Path.is_dir() on the portably-forward-slashed path.
    for gp_s, _ in git_porcelain_files():
        gp = Path(gp_s.replace("\\", "/"))
        try:
            if Path(gp_s.replace("\\", os.sep)).is_dir():
                out[gp_s].extend(sorted(Path(gp_s.replace("\\", os.sep)).rglob("*")))
        except OSError:
            pass
    return out


def _esc_regex(text: str) -> str:
    return re.escape(text)


def transform_bundle_name(basename: str) -> str:
    """evaluation/build_label_baseline.py  ->  evaluation/build_label_baseline"""
    return re.sub(r"\.py$", "", basename)


def emit_plan(root: Path) -> None:
    disk = disk_dir_as_files(root)
    porcelain = git_porcelain_files()

    # Build a consistent view: for each dir-as-file path (git porcelain basename
    # that matches a disk directory), gather children on disk AND in git porcelain.
    plans: list[tuple[str, list[Path], list[tuple[str, str]]]] = []

    for dirlike, children in disk.items():
        # dirlike is the path git porcelain calls a file; children is list of
        # real disk files living under a real disk directory with that stem.
        git_children: list[tuple[str, str]] = []
        for gpath, gstatus in porcelain:
            g = Path(gpath.replace("\\", "/"))
            # git porcelain item is 'inside' this dir-as-file if its path starts
            # with the dirlike path (and is not the dirlike path itself).
            if str(g).startswith(dirlike + os.sep) or str(g).startswith(dirlike + "/"):
                git_children.append((gpath, gstatus))

        children_on_disk = sorted({str(c) for c in children})
        plans.append((dirlike, children_on_disk, git_children))

    if not plans:
        print("No dir-as-file artifacts detected anywhere in the repo.", file=sys.stderr)
        return

    for dirlike, disk_children, git_children in plans:
        bundle_dir = root / transform_bundle_name(dirlike)
        print(f"\n=== Dir-as-file artifact: {dirlike} ===")
        print(f"Disk children ({len(disk_children)}):")
        for c in disk_children:
            print(f"  {c.relative_to(root)}")
        if git_children:
            print(f"Git porcelain items inside this artifact ({len(git_children)}):")
            for gpath, gstatus in git_children:
                print(f"  [{gstatus}] {gpath}")
        else:
            print("Git porcelain items inside: (none — only the broken dir-as-file entry)")

        print(f"Suggested bundle directory: {bundle_dir}")
        print("Suggested contents (one-per-line, relative to repo root):")
        for c in disk_children:
            dest = bundle_dir / c.relative_to(root)
            # avoid printing paths under bundle_dir when it already exists on disk
            if not dest.exists() or dest.is_file():
                print(f"  {dest}")
        print()

        # Plan, without staging/committing
        print("Plan (not applied):")
        action_lines: list[str] = []
        # If git porcelain has the same children (same relative path under the
        # artifact dir) they are already named correctly and we only need to
        # relocate the broken top-level entry.
        for gpath, gstatus in git_children:
            rel = Path(gpath.replace("\\", "/")).relative_to(root).as_posix()
            # expected: dirlike/<rest...>  where dirlike is the broken path
            print(f"  git porcelain keeps {rel!r} (status {gstatus!r}) as-is — already inside bundle dir.")
        # for children on disk NOT already listed in git porcelain, they need to be
        # moved into the bundle dir.
        already = {Path(g.replace("\\", "/")).relative_to(root).as_posix() for g, _ in git_children}
        for c in disk_children:
            rel = c.relative_to(root).as_posix()
            if rel not in already and c.parent != bundle_dir:
                dest = bundle_dir / c.relative_to(root)
                action_lines.append(f"mkdir -p {dest.parent}")
                action_lines.append(f"mv -f {c.as_posix()} {dest.as_posix()!r}")
        if action_lines:
            print("Disk side-effect (bash, copy-paste to apply):")
            for a in action_lines:
                print(a)
        else:
            print("Disk side-effect: none — children on disk already match bundle layout.")

        print("Git side-effect (ready to stage, not applied):")
        if not git_children:
            print("# remove broken dir-as-file entry (git porcelain file -> dir)")
            _esc_dir = dirlike.replace("\\", "/")
            _esc_dir = _esc_dir.rstrip(os.sep)
            print(f"git update-index --add -- {_esc_dir!r}")
        else:
            print("# git porcelain already reflects bundle contents; only the top entry needs fixing")
            _esc_dir = dirlike.replace("\\", "/")
            _esc_dir = _esc_dir.rstrip(os.sep)
            print(f"git update-index --add -- {_esc_dir!r}")
        print()


def emit_bash_fix_snippet(root: Path) -> None:
    """Emit a strict bash snippet that repairs broken filesystem entries.

    This is defensive only: it is NOT executed by this tool.  It is written so
    that if the repo was left with unreadable/empty directory entries where real
    content lives, an operator can paste the snippet to recover.
    """
    print("\n--- Bash recovery snippet (DO NOT run unless the repo is broken) ---\n")
    for dirlike, children in disk_dir_as_files(root).items():
        bundle_dir = root / transform_bundle_name(dirlike)
        # only emit recovery for dir-as-file cases
        if not (root / dirlike.replace("\\", os.sep)).is_dir():
            continue
        print(f"# Artifact: {dirlike}")
        print(f"bundle_dir={bundle_dir.resolve()}")
        print(f"mkdir -p {bundle_dir!r}")
        for c in sorted(children):
            dest = bundle_dir / c.relative_to(root)
            print(
                f"test -f {c.as_posix()!r} && mkdir -p {dest.parent.as_posix()!r} && mv -f {c.as_posix()!r} {dest.as_posix()!r}",
            )
        _esc_dir = dirlike.replace("\\", "/")
        _esc_dir = _esc_dir.rstrip(os.sep)
        print(f"# Then stage: git update-index --add -- {_esc_dir!r}")
        print()


if __name__ == "__main__":
    emit_plan(ROOT)
    emit_bash_fix_snippet(ROOT)
