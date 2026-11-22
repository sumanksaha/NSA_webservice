"""Run the answer-shape arms as a paired experiment, then score them.

The harness (``evaluation/rerun_gold_in_prompt.py``) is resumable: a record
that came back with an LLM error is retried on the next run, because a failed
call frozen into a tag silently biases the arm against the qids that happened
to 429.  So this runner is safe to re-run -- it skips arms that are already
complete and retries the ones that are not.

Order matters.  The **control replicate** comes first, because no candidate
delta is interpretable until the same-config spread on this path is known:

1. ``prod_r1`` / ``prod_r2`` / ``prod_r3`` -- three runs of the HEAD control
   (shipped template, single-pass, W4 on, answer contract + repair on) on the
   same qids.  Their spread IS the noise floor.
2. the candidate arms, on the same qids, paired against the control.
3. scoring under gate set v2, with the measured band printed beside it.

Pacing: the free OpenRouter pool throttles hard.  On 2026-11-10 two
concurrent runs exhausted the shared pool and every subsequent call returned
429, emptying the arms.  One run at a time, concurrency 1, is the rate the
pool tolerates.

Usage:
    python -m evaluation.run_shape_arms                 # everything
    python -m evaluation.run_shape_arms --only as25_armshape
    python -m evaluation.run_shape_arms --score-only    # re-score, no generation
    python -m evaluation.run_shape_arms --preflight     # check the model, then stop
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

RERUN_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "rerun_gip"

#: The control replicates.  Same configuration, different runs: their spread
#: is the noise floor every candidate delta has to clear.
CONTROL_TAG = "prod_r1"
CONTROL_REPLICATES = ("prod_r1", "prod_r2", "prod_r3")

#: The candidate arms, paired against ``CONTROL_TAG``.
ARMS: tuple[tuple[str, str], ...] = (
    ("as25_armshape", "armshape"),
    ("as25_armshape_v2", "armshape_v2"),
)

COHORT = "failures"
LIMIT = 25


def _harness_cmd(tag: str, variant: str, *, limit: int, concurrency: int, delay: float) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "evaluation.rerun_gold_in_prompt",
        "--tag",
        tag,
        "--limit",
        str(limit),
        "--cohort",
        COHORT,
        # The live .env sets ENABLE_STRUCTURED_REASONER=true and the harness
        # loads .env with override=True.  The treatment stack is the
        # single-pass grounded_qa template, so W3 must be explicitly off --
        # with W3 on, GROUND_QA_USER_TEMPLATE is never rendered and the arm
        # would be a silent no-op.
        "--no-structured-reasoner",
        "--concurrency",
        str(concurrency),
        "--delay",
        str(delay),
    ]
    if variant:
        cmd += ["--template-variant", variant]
    return cmd


def _records(tag: str) -> list[dict]:
    path = RERUN_DIR / f"{tag}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _status(tag: str, limit: int) -> tuple[int, int, list[str]]:
    """(ok, expected, errored qids) for one arm."""
    by_qid = {str(r.get("qid")): r for r in _records(tag)}
    errs = [q for q, r in by_qid.items() if r.get("error")]
    ok = sum(1 for r in by_qid.values() if not r.get("error"))
    return ok, limit, errs


def preflight() -> bool:
    """One live call.  Returns False when the model is throttled or broken."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    os.environ["RAG_USE_STUB_LLM"] = "false"
    from evaluation.llm_ssl_client import SSLBypassLLMClient

    try:
        resp = SSLBypassLLMClient().call("You are a legal assistant.", "Reply with exactly: OK")
    except Exception as exc:
        print(f"preflight: client raised {type(exc).__name__}: {exc}")
        return False
    if resp.error:
        print(f"preflight: FAILED -- {resp.error}")
        print("The free pool is throttled or the model is unavailable.")
        print("Nothing was generated. Try again later, or set RAG_LLM_MODEL to")
        print("another allow-listed id (see app/shared/llm_client.py ALLOWED_MODELS)")
        print("and re-run this script -- the arms are resumable, so only the")
        print("missing qids will be generated.")
        return False
    print(f"preflight: OK (model={resp.model}, latency={resp.latency:.1f}s)")
    return True


def _score() -> None:
    base = CONTROL_TAG
    arms = [t for t, _ in ARMS if _status(t, LIMIT)[0]]
    if not arms:
        print("scoring: no treatment arm has usable records yet -- nothing to pair against the control")
        return
    print("\nscoring: paired comparison under gate set v2")
    subprocess.run(  # noqa: S603 - argv[0] is sys.executable; the rest are module names and this script's own parameters
        [
            sys.executable,
            "-m",
            "evaluation.compare_rerun_arms",
            "--base",
            base,
            "--arms",
            ",".join(arms),
            "--cohort",
            "all",
            "--by-action",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    print("\nscoring: the noise floor every delta has to clear")
    subprocess.run(  # noqa: S603 - argv[0] is sys.executable; the rest are module names and this script's own parameters
        [
            sys.executable,
            "-m",
            "evaluation.noise_floor",
            "--tags",
            ",".join(CONTROL_REPLICATES),
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )
    print("\nscoring: answer-shape counterfactual on every arm")
    subprocess.run(  # noqa: S603 - argv[0] is sys.executable; the rest are module names and this script's own parameters
        [
            sys.executable,
            "-m",
            "evaluation.answer_shape_counterfactual",
            "--tags",
            ",".join([base, *arms]),
            "--by-action",
        ],
        cwd=PROJECT_ROOT,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="run a single arm tag")
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between completed calls")
    parser.add_argument("--score-only", action="store_true", help="re-score cached arms, no generation")
    parser.add_argument("--preflight", action="store_true", help="check the model answers, then stop")
    parser.add_argument("--no-preflight", action="store_true", help="skip the preflight call")
    args = parser.parse_args()

    if args.preflight:
        return 0 if preflight() else 1

    if args.score_only:
        _score()
        return 0

    if not args.no_preflight and not preflight():
        return 1

    plan: list[tuple[str, str]] = [(t, "") for t in CONTROL_REPLICATES] + list(ARMS)
    if args.only:
        plan = [(t, v) for t, v in plan if t == args.only]
        if not plan:
            print(f"unknown arm {args.only!r}; known: {[t for t, _ in plan]}")
            return 1

    for tag, variant in plan:
        ok, expected, errs = _status(tag, args.limit)
        if ok >= expected and not errs:
            print(f"\n=== {tag}: complete ({ok}/{expected}) -- skipping", flush=True)
            continue
        print(
            f"\n=== {tag}: {ok}/{expected} usable"
            + (f", retrying {errs}" if errs else "")
            + f"  [template={variant or 'shipped'}]",
            flush=True,
        )
        subprocess.run(  # noqa: S603 - argv[0] is sys.executable; the rest are module names and this script's own parameters
            _harness_cmd(tag, variant, limit=args.limit, concurrency=args.concurrency, delay=args.delay),
            cwd=PROJECT_ROOT,
            check=False,
        )
        time.sleep(5)

    _score()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
