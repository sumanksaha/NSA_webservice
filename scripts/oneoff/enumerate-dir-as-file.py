#!/usr/bin/env python3 -S
from __future__ import annotations

import os
from pathlib import Path

REPO = Path.cwd().resolve()
print("REPO:", REPO)
print()

found: list[tuple[str, list[str]]] = []

for root, dirs, files in os.walk(REPO):
    for d in dirs:
        full = (Path(root) / d).resolve()
        try:
            rel = full.relative_to(REPO).as_posix()
        except ValueError:
            continue
        if rel.endswith(".py"):
            # disk directory that looks like a .py file path -> artifact
            sub: list[str] = []
            try:
                for ch in sorted(full.iterdir()):
                    if ch.is_file():
                        sub.append(ch.relative_to(REPO).as_posix())
                    else:
                        sub.append(f"{ch.relative_to(REPO).as_posix()}/")
            except PermissionError:
                sub.append("<permission denied>")
            found.append((rel, sub))
            print(f"DIR-AS-FILE on disk: {rel}")
            print("  disk.isdir: True")
            for ch in sub:
                print(f"    {ch}")
            print()

if not found:
    print("No dir-as-file artifacts found on disk.")
else:
    print(f"\nTotal dir-as-file artifacts: {len(found)}")
