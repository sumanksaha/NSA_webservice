"""Verify the HF Serverless Inference API serves the pushed cross-encoder.

Hits ``https://api-inference.huggingface.co/models/<repo>`` with the three
sanity pairs (``query [SEP] text`` — cross-encoders are served as
``text-classification``) and compares the returned logits to the scores the
local checkpoint produces.  Run from a machine that can reach
``api-inference.huggingface.co`` (the sandboxed dev environment cannot).

Usage::

    HF_TOKEN=hf_xxx python scripts/test_hf_inference.py
    python scripts/test_hf_inference.py --repo sumanksaha/Foodmultidomain --token hf_xxx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

DEFAULT_REPO = "sumanksaha/Foodmultidomain"

#: (query, text) pairs + the local checkpoint scores (parity reference).
SANITY_PAIRS = [
    (("penalty for selling substandard food", "Section 50: General penalty for unsafe food"), -0.821),
    (("who appoints the Food Safety Officer", "Section 9: Officer of the Food Authority"), 1.071),
    (("prohibition on sale of adulterated food", "Section 21: Prohibition of misleading claims"), -8.424),
]

TOLERANCE = 0.15  # serverless may run fp16 — allow small drift


def _infer(url: str, token: str | None, query: str, text: str) -> float:
    body = json.dumps({"inputs": f"{query} [SEP] {text}"}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return float(data[0].get("score", 0.0))
    raise RuntimeError(f"unexpected response: {data!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify HF Serverless Inference serves the CE model")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--token", help="HF token (defaults to HF_TOKEN env)")
    args = parser.parse_args()

    token = args.token or os.environ.get("HF_TOKEN")
    url = f"https://api-inference.huggingface.co/models/{args.repo}"
    print(f"HF Serverless Inference check - {args.repo}")
    print("=" * 60)

    ok = True
    for (query, text), expected in SANITY_PAIRS:
        try:
            score = _infer(url, token, query, text)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  FAIL {query[:34]!r:36} -> error: {exc}")
            ok = False
            continue
        match = abs(score - expected) <= TOLERANCE
        ok = ok and match
        print(
            f"  {'OK' if match else 'MISMATCH'} {query[:34]!r:36} -> {score:+.3f}"
            f" (local {expected:+.3f})"
        )

    print("=" * 60)
    print(f"Summary: {'PASS - serverless inference serves your weights' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
