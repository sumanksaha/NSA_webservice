#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


def scan_templates(root: Path) -> list[dict]:
    findings = []
    patterns = [
        re.compile(r"You are a.*assistant", re.I),
        re.compile(r"System:", re.I),
        re.compile(r"Instructions:", re.I),
    ]
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for p in patterns:
            for m in p.finditer(text):
                findings.append({"file": str(path), "match": m.group(0), "pos": m.start()})
    return findings


def main() -> None:
    root = Path(__file__).parent.parent
    findings = scan_templates(root / "app")
    for f in findings[:50]:
        print(f"{f['file']}:{f['pos']}: {f['match']}")


if __name__ == "__main__":
    main()
