#!/usr/bin/env python3 -S
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path.cwd().resolve()


def porcelain_status_map() -> dict[str, str]:
    raw = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        status = line[0:2].replace(" ", ".")
        path = line[3:].strip()
        out[path.replace("\\", "/")] = status
    return out


def disk_children_under_dirlike(porcelain_path: str) -> list[Path]:
    candidate = REPO_ROOT / porcelain_path.replace("\\", "/")
    if not candidate.is_dir():
        return []
    return sorted(
        [c for c in candidate.rglob("*") if c.is_file()],
        key=lambda p: str(p.relative_to(REPO_ROOT).as_posix()),
    )


def bundle_dir_for(porcelain_path: str) -> Path:
    p = Path(porcelain_path.replace("\\", "/"))
    if p.suffix == ".py":
        return p.with_suffix("")
    return p


def is_under_any_dirlike_as_file(porcelain_path: str, dirlike_set: set[str]) -> bool:
    # porcelain_path is repo-relative, git reported it as a file.  If it lives
    # under a dirlike path (git reported that parent as a dir-as-file), treat
    # it as already relocated.
    for d in dirlike_set:
        if porcelain_path == d or porcelain_path.startswith(d + "/"):
            return True
    return False


def main() -> int:
    porcelain = porcelain_status_map()
    dirlike = {path for path, status in porcelain.items() if Path(path).is_dir()}
    if not dirlike:
        print("No dir-as-file artifacts detected on disk.", file=sys.stderr)
        # still run self-test and finish cleanly
        run_self_test()
        return 0

    print("Dir-as-file artifacts detected on disk:", file=sys.stderr)
    for d in sorted(dirlike):
        print(f"  {d}")
        children = disk_children_under_dirlike(d)
        if children:
            print(f"    children on disk ({len(children)}):")
            for c in children:
                print(f"      {c.relative_to(REPO_ROOT).as_posix()!s}")
        else:
            print("    (no children on disk)")

    # Plan: for each dirlike path, the bundle dir is dirlike.with_suffix("")
    # For each child on disk that is NOT under a different dirlike path as a file,
    # move it into the bundle dir (preserving relative structure).  For each child
    # that already lives under a dirlike-as-file (i.e. git porcelain says both
    # parent and child are dir-as-files), leave it — reloc can loop.
    dirlike_set = set(dirlike)
    moves: list[tuple[Path, Path]] = []  # (src, dest) repo-relative

    for d in dirlike:
        bundle = bundle_dir_for(d)
        src = REPO_ROOT / d
        dest = REPO_ROOT / bundle
        dest.mkdir(parents=True, exist_ok=True)
        for child in disk_children_under_dirlike(d):
            child_rel = child.relative_to(REPO_ROOT).as_posix()
            # already under a dirlike? skip
            if is_under_any_dirlike_as_file(child_rel, dirlike_set - {d}):
                continue
            target = (REPO_ROOT / bundle / child_rel).with_suffix("")
            if target.suffix == "" and not child_rel.endswith(".py"):
                target = target.with_name(child_rel.split("/")[-1].replace(".py", ""))
            else:
                target = target.with_name(child_rel.split("/")[-1].replace(".py", ""))
            target_dir = target if target.is_dir() else target.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            if child != target:
                moves.append((child, target))
                print(f"  move: {child_rel} -> {target.relative_to(REPO_ROOT).as_posix()!s}")

    if moves:
        print("\nApplying moves on disk ...", file=sys.stderr)
        for src, dest in moves:
            src_path = REPO_ROOT / src
            dest_path = REPO_ROOT / dest
            if dest_path.exists():
                if dest_path.is_dir():
                    # enumerate any pre-existing file of same name
                    pass
                else:
                    os.remove(dest_path)
            if src_path.is_file():
                shutil.move(str(src_path), str(dest_path))
                print(f"    MOVED: {src} -> {dest}")
            else:
                print(f"    SKIP (src not a file): {src}")

    # Now fix git porcelain: the broken file entries (now dirs) must be updated so
    # git no longer thinks the dirlike path is a file.  Do this via
    # `git update-index --add -- <dirlike>` which replaces a file entry with a
    # directory entry in the index.  We do NOT stage other new files here — that
    # would violate "do not stage anything beyond the dir-as-file artifacts".
    print("\nGit side-effect plan (NOT applied):", file=sys.stderr)
    for d in sorted(dirlike):
        bundle = bundle_dir_for(d)
        print(f"  git update-index --add --working-tree {d}")
        print(f"  # verified on disk: {bundle} exists = {(REPO_ROOT / bundle).exists()}")

    # Self-test after moves
    run_self_test()
    return 0


def run_self_test() -> None:
    print("\n--- POST-MOVE SELF-TEST (not applied — simulation) ---", file=sys.stderr)
    # After moves, verify no path git porcelain reports as a file that is a dir on
    # disk.  We re-read porcelain for that.
    porcelain = porcelain_status_map()
    violations = [p for p in porcelain if Path(p.replace("\\", "/")).is_dir()]
    if violations:
        print("VERIFICATION FAILED: still dir-as-file after move:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
    else:
        print("VERIFICATION OK: no dir-as-file artifacts remain on disk.", file=sys.stderr)
    print("VERIFICATION DONE.\n", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
