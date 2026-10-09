#!/usr/bin/env python3 -S
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path.cwd()

porcelain = sys.argv[1] if len(sys.argv) > 1 else "."

p = Path(porcelain).relative_to(REPO.resolve()) if Path(porcelain).is_absolute() else Path(porcelain)

print("porcelain raw:", repr(p.as_posix()))
print("exists:", p.exists(), "is_dir:", p.is_dir(), "is_file:", p.is_file())
print("abspath:", p.resolve())
print("name parts:", list(p.parts))

# enumerate dir-as-file candidates: any Path whose repo-relative string
# ends with .py but which on disk is itself a directory.
for cand in REPO.rglob("*"):
    if cand.is_dir():
        rel = cand.relative_to(REPO).as_posix()
        if rel.endswith(".py"):
            print(f"DIR-AS-FILE on disk: {rel}")
print("done")
