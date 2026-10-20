"""Reasoning-lever experiment on minimax (free): does turning reasoning off
remove the gain the 150-QID run measured?

Two questions, one harness:

1. CHANNEL: does the model actually stop thinking when asked, and does
   ``content`` become non-null (i.e. does the P0 defect disappear)?
2. QUALITY: on the same questions and the same retrieval pools, how does the
   scored answer move under each lever, against the cached 1024/reasoning-on
   baseline?

Variants (all on the production single-pass grounded path):

    base          1024 tokens, no reasoning params      -> cached control
    cap4096       4096 tokens, no reasoning params
    cap8192       8192 tokens, no reasoning params (finishes; slow)
    think1k       4096 tokens, reasoning capped at 1024
    think2k       4096 tokens, reasoning capped at 2048
    think512      2048 tokens, reasoning capped at 512
    noreason      reasoning={"enabled": False}  -> HTTP 400 on Minimax
    include_off   include_reasoning=False (hides the trace, does not stop it)

``include_reasoning`` only hides the trace; it does not stop the tokens being
generated.  It is in the probe so the difference is measured, not assumed.

Usage:
    python -m evaluation.exp_reasoning --mode probe
    python -m evaluation.exp_reasoning --mode run --variants cap4096,noreason
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
CACHE = OUT / "reasoning_exp.jsonl"
BASE_CACHE = OUT / "model_ab.jsonl"
MODEL = "minimax/minimax-m3.1-flash:free"
SHORT = "minimax-m3.1-flash"

#: 8 questions that supplied most of the measured gain.
TOP8 = ["Q130", "Q031", "Q118", "Q058", "Q111", "Q053", "Q124", "Q033"]
#: the 4 must-hold veto questions.
MUST4 = ["Q052", "Q015", "Q047", "Q002"]
#: 6 insufficient-evidence questions (abstention credit is scored here).
IE6 = ["Q133", "Q135", "Q128", "Q098", "Q042", "Q060"]
#: failure-cohort questions where the baseline moved both ways.
FAIL6 = ["Q016", "Q014", "Q040", "Q007", "Q025", "Q032"]

QIDS = TOP8 + MUST4 + IE6 + FAIL6

#: Call kwargs per variant.  These are passed through ``call(**params)`` ->
#: ``_real_call(**extra)`` -> ``body.update(extra)``.
#:
#: Two facts learned from the raw probe (2026-10-20) shape this list:
#:   * ``reasoning={"enabled": False}`` is rejected by Minimax with HTTP 400
#:     ("Reasoning is mandatory for this endpoint and cannot be disabled"), so
#:     there is no reasoning-off variant to test.
#:   * ``reasoning={"max_tokens": N}`` caps the thinking budget and leaves the
#:     remainder of ``max_tokens`` for the answer.  This is the only lever that
#:     ends the trace before the cap, other than giving it 8192 tokens.
VARIANTS = {
    "base": {},
    "cap4096": {"max_tokens": 4096},
    "cap8192": {"max_tokens": 8192},
    "think1k": {"max_tokens": 4096, "reasoning": {"max_tokens": 1024}},
    "think2k": {"max_tokens": 4096, "reasoning": {"max_tokens": 2048}},
    "think512": {"max_tokens": 2048, "reasoning": {"max_tokens": 512}},
    "noreason": {"reasoning": {"enabled": False}},
    "include_off": {"include_reasoning": False},
}

TIMEOUT_S = 60.0  # eval parity; latency >30 s is reported against production


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def base_rows() -> dict[str, dict]:
    best: dict[str, dict] = {}
    for r in _load(BASE_CACHE):
        if r.get("cond") != SHORT:
            continue
        if r["qid"] not in best or (best[r["qid"]].get("error") and not r.get("error")):
            best[r["qid"]] = r
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["probe", "run"], default="probe")
    ap.add_argument("--variants", default="cap4096,noreason")
    ap.add_argument("--qids", default="")
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    args = ap.parse_args()

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in VARIANTS:
            raise SystemExit(f"unknown variant {v!r}; pick from {sorted(VARIANTS)}")

    qids = [q.strip() for q in args.qids.split(",") if q.strip()]
    if not qids:
        qids = ["Q031"] if args.mode == "probe" else QIDS

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["RAG_USE_STUB_LLM"] = "false"
    os.environ["APP_LAZY_IMPORT"] = "true"

    import httpx
    from app import create_app
    from app.rag.generation.llm_client import GroundedLLMClient, GroundedLLMResponse

    class RecordingClient(GroundedLLMClient):
        """Production call path, but keeps the channel facts it normally drops."""

        def __init__(self, *a, timeout_s: float = TIMEOUT_S, **kw):
            super().__init__(*a, **kw)
            self.timeout_s = timeout_s
            self.last: dict = {}

        def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
            start = time.perf_counter()
            url = self._base_url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://nsa-webservice.local",
                "X-Title": "NSA Webservice Reasoning Experiment",
            }
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            body.update(extra)

            last_exc = None
            for attempt in range(3):
                try:
                    with httpx.Client(timeout=self.timeout_s, verify=False) as client:
                        resp = client.post(url, headers=headers, json=body)
                        resp.raise_for_status()
                        data = resp.json()
                        choice = data["choices"][0]
                        msg = choice.get("message", {}) or {}
                        content = msg.get("content")
                        reasoning = msg.get("reasoning")
                        usage = data.get("usage", {}) or {}
                        served = content if content is not None else (reasoning or "")
                        self.last = {
                            "content_len": len(content or ""),
                            "reasoning_len": len(reasoning or ""),
                            "content_null": content is None,
                            "finish_reason": choice.get("finish_reason"),
                            "native_finish_reason": choice.get("native_finish_reason"),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                                "reasoning_tokens", 0
                            ),
                            "cost": usage.get("cost"),
                            "usage_raw": usage,
                        }
                        return GroundedLLMResponse(
                            text=served,
                            model=self.model,
                            usage={
                                "prompt_tokens": usage.get("prompt_tokens", 0),
                                "completion_tokens": usage.get("completion_tokens", 0),
                                "total_tokens": usage.get("total_tokens", 0),
                            },
                            latency=time.perf_counter() - start,
                        )
                except Exception as exc:  # noqa: BLE001 - eval harness
                    last_exc = exc
                    if attempt < 2:
                        rate = "429" in str(exc) or "rate" in str(exc).lower()
                        time.sleep((20 * (2**attempt)) if rate else 2**attempt)
            return GroundedLLMResponse(
                error=f"LLM request failed after 3 attempts: {last_exc}",
                model=self.model,
                latency=time.perf_counter() - start,
            )

    import tempfile

    scratch = Path(tempfile.mkdtemp(prefix="reasoning_exp_")) / "eval.db"
    app = create_app(db_uri=f"sqlite:///{scratch}")
    with app.app_context():
        from evaluation.benchmark import load_questions
        from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index
        from evaluation.reeval_89 import _grade
        from app.rag.generation import GroundedGenerationService
        from app.rag.generation.context_builder import ContextBuilder
        from app.rag.generation.llm_client import GroundedLLMResponse

        class VariantService(GroundedGenerationService):
            """The production service, with the variant's call kwargs wired in.

            This is the exact seam the product change will use (``_call_llm``
            passing ``max_tokens`` / ``reasoning`` from settings), so the
            experiment measures the shipped path, not a parallel one.
            """

            def __init__(self, *a, params=None, **kw):
                super().__init__(*a, **kw)
                self._params = dict(params or {})

            def _call_llm(self, system_prompt, user_prompt):
                try:
                    return self.llm_client.call(
                        system_prompt, user_prompt, **self._params
                    )
                except Exception as exc:  # noqa: BLE001
                    return GroundedLLMResponse(error=str(exc))

        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        retr = {r["qid"]: r for r in _load(OUT / "retrieval.jsonl")}

        existing = {(r["qid"], r["variant"]) for r in _load(CACHE)}
        todo = [
            (q, v)
            for v in variants
            for q in qids
            if (q, v) not in existing and q in questions
        ]
        print(f"model={MODEL} qids={len(qids)} variants={variants} to_run={len(todo)}", flush=True)

        with open(CACHE, "a", encoding="utf-8") as out:
            for i, (qid, variant) in enumerate(todo, 1):
                q = questions[qid]
                ids = list((retr.get(qid, {}).get("C_hybrid") or {}).get("chunk_ids") or [])[:20]
                chunks = _chunks_for(ids, payload_index)
                client = RecordingClient(model=MODEL, timeout_s=args.timeout)
                service = VariantService(
                    llm_client=client,
                    context_builder=ContextBuilder(),
                    params=VARIANTS[variant],
                )
                qt = q.question_types[0] if q.question_types else ""
                t0 = time.monotonic()
                try:
                    resp = service.generate(q.question, chunks, query_type=qt)
                    answer = resp.answer or ""
                    err = (resp.debug or {}).get("error")
                except Exception as exc:  # noqa: BLE001
                    answer, err = "", f"{type(exc).__name__}: {exc}"
                latency = round(time.monotonic() - t0, 2)
                grade = _grade(q, answer)
                rec = {
                    "qid": qid,
                    "variant": variant,
                    "model": MODEL,
                    "params": VARIANTS[variant],
                    "n_chunks": len(chunks),
                    "answer_length": len(answer),
                    "latency_s": latency,
                    "error": err,
                    "answer": answer,
                    "ie": bool(q.insufficient_evidence),
                    **client.last,
                    **grade,
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(
                    f"[{i}/{len(todo)}] {qid} [{variant}] content={rec.get('content_len')} "
                    f"reason={rec.get('reasoning_len')} finish={rec.get('finish_reason')} "
                    f"rtok={rec.get('reasoning_tokens')} soft={grade.get('answer_correctness')} "
                    f"bin={grade.get('binary_correct')} lat={latency}s err={err or ''}",
                    flush=True,
                )
                if args.delay:
                    time.sleep(args.delay)

    return 0


def report() -> None:
    rows = _load(CACHE)
    if not rows:
        print("no experiment rows yet")
        return
    base = base_rows()
    variants = sorted({r["variant"] for r in rows})
    print(f"\n{'=' * 96}\nREASONING LEVER EXPERIMENT ({len(rows)} rows)\n{'=' * 96}")
    for v in variants:
        got = [r for r in rows if r["variant"] == v and not r.get("error")]
        if not got:
            continue
        n = len(got)
        print(f"\n{v}: n={n}")
        print(
            f"  content_null {sum(1 for r in got if r.get('content_null'))}/{n}"
            f"  finish={_count(got, 'finish_reason')}"
            f"  reason_tokens mean {st.mean([r.get('reasoning_tokens') or 0 for r in got]):.0f}"
            f"  completion mean {st.mean([r.get('completion_tokens') or 0 for r in got]):.0f}"
        )
        print(
            f"  soft {st.mean([r['answer_correctness'] for r in got]):.4f}"
            f"  bin {sum(1 for r in got if r.get('binary_correct'))}/{n}"
            f"  credit {sum(1 for r in got if r.get('binary_correct_abstention_credit'))}"
            f"  len {st.mean([r['answer_length'] for r in got]):.0f}"
            f"  lat mean {st.mean([r['latency_s'] for r in got]):.1f}s"
            f"  >30s {sum(1 for r in got if r['latency_s'] > 30)}"
        )
        pairs = [(base[r["qid"]], r) for r in got if r["qid"] in base]
        if pairs:
            d = [r["answer_correctness"] - b["answer_correctness"] for b, r in pairs]
            print(
                f"  vs cached base n={len(pairs)}  soft {st.mean([b['answer_correctness'] for b, _ in pairs]):.4f}"
                f" -> {st.mean([r['answer_correctness'] for _, r in pairs]):.4f} ({st.mean(d):+.4f}; "
                f"{sum(1 for x in d if x > 0.0005)}W/{sum(1 for x in d if x < -0.0005)}L)"
            )
    print()


def _count(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        out[r.get(key)] = out.get(r.get(key), 0) + 1
    return out


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
        raise SystemExit(0)
    raise SystemExit(main())
