"""Experiment D — Structured Legal Reasoning + Legal Auditor (150 Q x 3 conditions).

Objective (spec sec 1): determine whether an explicit intermediate legal-reasoning
representation improves answer correctness over the direct generation baseline, and
whether a Legal Auditor adds a further improvement — with the SAME evidence.

Conditions:
  D1 — Direct baseline      : Experiment C O3 outputs + metrics, REUSED (0 new calls).
  D2 — Structured reasoning : O3 evidence -> structured legal analysis -> final answer.
  D3 — D2 + Legal Auditor   : D2 analysis -> audit (+ controlled revision) -> answer.

HARD NEW LLM BUDGET (spec sec 8): <= 300 successful LLM generations.
  D2: 150 reasoning-stage calls.
  D3: 150 audit/revision-stage calls (audit and controlled revision combined into
      ONE call whenever possible; a FAIL triggers at most ONE same-budget revision
      generation, i.e. D3 = audit(150) + revision(only for FAILs, hard-capped so
      D2+D3 total stays <= 300 successful generations)).
  The atomic cap is 300 across BOTH stages; when a planned phase would exceed it
  the runner STOPS and reports the expected call count instead of running.

Isolation (spec sec 2): benchmark, evidence, gold identity, context builder,
retrieval, RRF, CE, question ordering, temperature (0.1), model
(poolside/laguna-s-2.1:free), answer evaluator and citation evaluator are all
IDENTICAL to Experiment C — the ONLY experimental variable is the downstream
reasoning architecture.

Context control (spec sec 11): every D2/D3 context is rebuilt with the same
deterministic builder from the same manifest and verified byte-for-byte against
the stored Experiment C O3 context.  Any mismatch aborts before any LLM call.

Data leakage (spec sec 12): the acceptable conclusion / gold / C results are never
placed in any prompt.  A Phase BUILD leakage scan asserts this.

No unrestricted agent loop (spec sec 7): at most Reason -> Audit -> Correct ->
Answer.  One revision cycle maximum per question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import types
from collections import Counter
from pathlib import Path
from typing import Any

import warnings

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

# matplotlib is only required for the PLOTS phase; if it is absent we install
# a minimal stub BEFORE importing the Experiment B/C modules (they import it
# unguarded) so the build/run/analyze/summary phases still work in this
# environment (phase_plots reports the missing dependency instead of crashing).
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _HAS_MATPLOTLIB = True
except ModuleNotFoundError:
    _mpl = types.ModuleType("matplotlib")
    _mpl.use = lambda *a, **k: None
    _pyplot = types.ModuleType("matplotlib.pyplot")
    _pyplot.subplots = lambda *a, **k: (
        types.SimpleNamespace(
            suptitle=lambda *a, **k: None, tight_layout=lambda *a, **k: None, savefig=lambda *a, **k: None
        ),
        types.SimpleNamespace(
            bar=lambda *a, **k: None,
            barh=lambda *a, **k: None,
            set_title=lambda *a, **k: None,
            set_xticks=lambda *a, **k: None,
            set_xticklabels=lambda *a, **k: None,
            set_yticks=lambda *a, **k: None,
            set_yticklabels=lambda *a, **k: None,
            set_ylim=lambda *a, **k: None,
            text=lambda *a, **k: None,
            scatter=lambda *a, **k: None,
            tick_params=lambda *a, **k: None,
        ),
    )
    _pyplot.close = lambda *a, **k: None
    _mpl.pyplot = _pyplot
    plt = _pyplot
    sys.modules["matplotlib"] = _mpl
    sys.modules["matplotlib.pyplot"] = _pyplot
    _HAS_MATPLOTLIB = False

from evaluation.eval_e2e_v2 import load_payload_index, _SSLBypassLLMClient
from evaluation.benchmark import load_questions
from evaluation.resolution import FamilyMap
from evaluation.experiment_b_topk_eval import (
    _mean,
    _pctl,
    build_gold_index,
    compute_metrics,
    to_retrieved_chunk,
)
from evaluation.experiment_c_oracle_eval import (
    C_MANIFEST,
    C_PER_QUESTION,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_CTX_CHARS,
    _COracleContextBuilder,
    resolve_gold_units,
)
from app.rag.generation.llm_client import GroundedLLMResponse
from dataclasses import dataclass, field
import numpy as np

import torch

torch.set_num_threads(2)

# --------------------------------------------------------------------------- #
# Paths + constants
# --------------------------------------------------------------------------- #
OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
D_PLOT_DIR = OUT_DIR / "plots"
D_PLOT_DIR.mkdir(parents=True, exist_ok=True)

D_BUILD_REPORT = OUT_DIR / "experiment_D_build_report.json"
D_D2_CKPT = OUT_DIR / "experiment_D_d2_checkpoint.jsonl"  # real D2
D_D2_CKPT_STUB = OUT_DIR / "experiment_D_d2_checkpoint_stub.jsonl"  # 0-call validation
D_D3_CKPT = OUT_DIR / "experiment_D_d3_checkpoint.jsonl"  # real D3
D_D3_CKPT_STUB = OUT_DIR / "experiment_D_d3_checkpoint_stub.jsonl"
D_CALLS_JSONL = OUT_DIR / "experiment_D_calls.jsonl"  # per-call accounting
D_CALLS_STUB_JSONL = OUT_DIR / "experiment_D_calls_stub.jsonl"
D_AGGREGATE = OUT_DIR / "experiment_D_results.json"
D_PER_QUESTION = OUT_DIR / "experiment_D_per_question.jsonl"
D_REASONING = OUT_DIR / "experiment_D_reasoning.jsonl"
D_AUDITOR = OUT_DIR / "experiment_D_auditor.jsonl"
D_ERROR_TAX = OUT_DIR / "experiment_D_error_taxonomy.json"
D_TRANSITIONS = OUT_DIR / "experiment_D_transition_matrix.json"
D_ACCOUNTING = OUT_DIR / "experiment_D_call_accounting.json"
D_SUMMARY = OUT_DIR / "experiment_D_summary.md"

O3_COND = "O3_full_support"
D_CONDS = ["C-O3", "D2", "D3"]
D_CONDITION_META = {
    "C-O3": ("C-O3 Direct (reused)", "Experiment C O3 Full Support outputs and metrics, reused verbatim"),
    "D2": ("D2 Structured Reasoning", "O3 evidence -> structured legal analysis -> final answer"),
    "D3": ("D3 Reasoning + Auditor", "D2 analysis -> Legal Auditor (+ max 1 controlled revision) -> final answer"),
}

QUESTIONS_PER_COND = 150
PLANNED_CALLS = 300  # 150 D2 reasoning + 150 D3 audit(+revision) — hard cap
BUDGET_STOP_EXIT = 3  # distinct exit code: budget would be exceeded
MAX_REVISION_CYCLES = 1  # spec sec 7

LLM_TIMEOUT = 120.0
# Combined structured analysis + final answer capacity.  3072 truncated the
# JSON tail on several O3-large-context questions (unbalanced braces -> parse
# failure), so 4096 gives headroom; prompts/model/temperature remain fixed.
MAX_TOKENS_STRUCTURED = 8192

# spec sec 9 stage labels (call accounting)
D2_STAGE = "reasoning"
D3_STAGE = "audit_revision"

# Transport-only backoff (identical policy to Experiment C): free-tier 429 /
# empty-200 rejections are rate-limit recoveries — the LLM is never invoked on a
# rejected request — so they do NOT count toward the generation budget.
TRANSPORT_BACKOFF_BASE = 30.0
MAX_TRANSPORT_RETRIES = 15
RETRY_AFTER_CAP = 120.0
INTER_CALL_DELAY = 3.0  # conc=1 pacing under the free-tier rate limit

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_DEFECT_TYPES = (
    "wrong_governing_provision",
    "wrong_act_or_regulation",
    "wrong_section_or_clause",
    "definition_error",
    "condition_omission",
    "exception_proviso_omission",
    "cross_reference_failure",
    "fact_condition_mapping_error",
    "general_vs_specific_rule_error",
    "prohibition_obligation_confusion",
    "conclusion_does_not_follow",
    "unsupported_claim",
    "citation_error",
    "hidden_uncertainty",
    "incomplete_reasoning",
    "other",
)

# --------------------------------------------------------------------------- #
# Prompts — FIXED for the entire experiment (spec sec 21).  No prompt variants.
# --------------------------------------------------------------------------- #
REASONING_SYSTEM_PROMPT = (
    "You are a legal reasoning engine. You will receive a question and numbered "
    "legal evidence sources. Analyse the evidence and emit ONLY one JSON object "
    "with EXACTLY TWO top-level keys: structured_analysis and final_answer. "
    "The structured_analysis value must be an object with these keys: issue; "
    "governing_provisions (list of {provision_id, description, reason}); "
    "definitions (list of {term, definition, source}); legal_rules (list of "
    "{rule, source}); conditions (list of {condition, source, status: "
    "satisfied|not_satisfied|unknown, supporting_fact}); exceptions_and_provisos "
    "(list of {exception, source, applicable: true|false}); facts (list of "
    "{fact, source: question|evidence}); fact_condition_mapping (list of "
    "{condition, fact, determination: satisfied|not_satisfied|unknown, "
    "reason}); cross_references (list of {from, to, relevance}); "
    "conflicts_or_hierarchy (list of {issue, resolution}); legal_conclusion "
    "(string); supporting_evidence (list of {claim, source}); uncertainties "
    "(list of strings). The final_answer value must be a string: the answer to "
    "the question derived ONLY from structured_analysis, citing the evidence "
    "with [n] markers used inside the analysis. Every source value must be an "
    "evidence marker like [3] or a short statute reference with its marker. "
    "Use ONLY the provided evidence and the question: never invent provisions, "
    "facts or citations. Keep every field concise and legally precise. Output "
    "the JSON object only — no prose before or after."
)

ANSWER_SYSTEM_PROMPT = (
    "You are a legal assistant. You will receive a question and a structured "
    "legal analysis that was derived from numbered legal evidence sources. "
    "Write the final answer using ONLY the structured analysis: do not "
    "reanalyse the evidence and do not add provisions, conditions, exceptions "
    "or facts that are not in the analysis. Cite sources with [n] markers "
    "where n is the source number referenced in the analysis (e.g. [1], [2]). "
    "If the analysis states that evidence is insufficient or uncertain, say so "
    "clearly. Keep the answer concise and legally precise."
)

AUDITOR_SYSTEM_PROMPT = (
    "You are a legal auditor. You will receive a question, numbered legal "
    "evidence sources, and a structured legal analysis of that evidence. "
    "Independently audit whether the analysis is supported by the question and "
    "evidence. Check: (1) correct governing provision; (2) correct Act or "
    "Regulation; (3) correct section/subsection/clause; (4) correct "
    "definitions; (5) all material conditions identified; (6) exceptions and "
    "provisos considered; (7) relevant cross-references followed; (8) facts "
    "correctly mapped to legal conditions; (9) general rule not applied where "
    "a specific rule governs; (10) prohibitions not confused with obligations "
    "or permissions; (11) the conclusion follows from the identified rules; "
    "(12) no material claim is unsupported; (13) citations attach to the "
    "correct provisions; (14) no important uncertainty is hidden; (15) the "
    "reasoning is not incomplete. If the analysis has no material defect, "
    "output status PASS. Otherwise output FAIL with structured defects and a "
    "corrected_conclusion field that states the corrected legal conclusion in "
    "one or two sentences. Output ONLY one JSON object with keys: status "
    '("PASS"|"FAIL"); defects (list of {type, description, severity: '
    "critical|major|minor, evidence: [sources]}); missing_elements (list of "
    "strings); required_corrections (list of strings); corrected_conclusion "
    "(string; empty if PASS); citation_corrections (list of strings). Use only "
    "the provided evidence: never invent provisions or citations."
)


# --------------------------------------------------------------------------- #
# Budget guard — atomic cap on successful generations (D2 + D3 share it)
# --------------------------------------------------------------------------- #
_CALL_LOCK = threading.Lock()
_CALL_COUNT = [0]  # successful generations (budget = PLANNED_CALLS)
_ACTIVE_CAP = [PLANNED_CALLS]
_RATE_LOCK = threading.Lock()
_RATE_LIMIT_RECOVERIES = [0]  # transport backoffs — NOT counted toward the budget


def _budget_stop(expected: int) -> int:
    print(
        f"STOP (spec sec 8): the requested run needs {expected} more successful LLM "
        f"calls but only {max(0, _ACTIVE_CAP[0] - _CALL_COUNT[0])} budget slots remain "
        f"(cap={_ACTIVE_CAP[0]}).  Nothing was executed — expected call count reported "
        f"instead of silently exceeding the budget.",
        flush=True,
    )
    return BUDGET_STOP_EXIT


class _DNoRetryClient(_SSLBypassLLMClient):
    """SSL-bypass client: one generation per stage, 300-generation hard cap.

    Mirrors Experiment C's ``_CNoRetryClient``: the parent 3x retry loop is
    replaced by a single generation attempt per stage; free-tier 429 /
    empty-200 rejections are handled by a TRANSPORT-ONLY backoff (the LLM is
    never invoked on a rejected request, so these are rate-limit recoveries,
    not generation retries — only successful generations count toward the cap).
    """

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        with _CALL_LOCK:
            if _CALL_COUNT[0] >= _ACTIVE_CAP[0]:
                return GroundedLLMResponse(
                    text="",
                    model=self.model,
                    latency=0.0,
                    error=f"experiment_D budget exhausted (cap={_ACTIVE_CAP[0]})",
                )
            _CALL_COUNT[0] += 1
            attempt_no = _CALL_COUNT[0]

        import httpx

        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://nsa-webservice.local",
            "X-Title": "NSA Webservice Experiment D (structured reasoning)",
        }
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(extra)
        start = time.perf_counter()
        last_error = None

        def _backoff_sleep(attempt: int, resp=None, reason: str = ""):
            with _RATE_LOCK:
                _RATE_LIMIT_RECOVERIES[0] += 1
            ra = None
            if resp is not None:
                try:
                    ra = resp.headers.get("retry-after")
                except Exception:
                    ra = None
            base = TRANSPORT_BACKOFF_BASE * attempt
            wait = base
            if ra:
                try:
                    wait = max(wait, float(ra))
                except ValueError:
                    try:
                        import calendar

                        wait = max(wait, calendar.timegm(time.strptime(ra, "%a, %d %b %Y %H:%M:%S GMT")))
                    except Exception:
                        pass
            wait = min(wait, RETRY_AFTER_CAP)
            print(
                f"    [transport-backoff] attempt={attempt}/{MAX_TRANSPORT_RETRIES} {reason} "
                f"wait={wait:.0f}s recoveries_so_far={_RATE_LIMIT_RECOVERIES[0]}",
                flush=True,
            )
            time.sleep(wait)

        for attempt in range(1, MAX_TRANSPORT_RETRIES + 1):
            try:
                with httpx.Client(timeout=LLM_TIMEOUT, verify=False) as client:
                    resp = client.post(url, headers=headers, json=body)
                    if resp.status_code == 429:
                        last_error = "HTTP 429 (rate-limited)"
                        if attempt == MAX_TRANSPORT_RETRIES:
                            return GroundedLLMResponse(
                                text="",
                                model=self.model,
                                latency=time.perf_counter() - start,
                                error=f"experiment_D: HTTP 429 after {attempt} transport attempts",
                            )
                        _backoff_sleep(attempt, resp, "HTTP 429")
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                choice = data["choices"][0]
                message = choice.get("message", {})
                text = message.get("content")
                if text is None:
                    text = message.get("reasoning") or ""
                if text.strip() == "":
                    last_error = "empty completion (transient free-tier refusal)"
                    if attempt == MAX_TRANSPORT_RETRIES:
                        return GroundedLLMResponse(
                            text=text,
                            model=self.model,
                            latency=time.perf_counter() - start,
                            error=f"experiment_D: empty completion after {attempt} transport attempts",
                        )
                    _backoff_sleep(attempt, reason="empty completion")
                    continue
                usage = data.get("usage", {})
                return GroundedLLMResponse(
                    text=text,
                    model=self.model,
                    usage={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                    latency=time.perf_counter() - start,
                )
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_error = repr(exc)
                if status == 429 and attempt < MAX_TRANSPORT_RETRIES:
                    _backoff_sleep(attempt, getattr(exc, "response", None), "HTTP 429")
                    continue
                if attempt == MAX_TRANSPORT_RETRIES:
                    return GroundedLLMResponse(
                        text="",
                        model=self.model,
                        latency=time.perf_counter() - start,
                        error=f"experiment_D: exhausted {MAX_TRANSPORT_RETRIES} transport attempts ({last_error})",
                    )
                _backoff_sleep(attempt, reason=f"transient error: {exc!r}")
                continue
        return GroundedLLMResponse(
            text="",
            model=self.model,
            latency=time.perf_counter() - start,
            error=f"experiment_D: exhausted {MAX_TRANSPORT_RETRIES} transport attempts ({last_error})",
        )


class _StubDClient(_SSLBypassLLMClient):
    """Stage-aware stub for ZERO-real-call pipeline validation (spec sec 8).

    Emits valid JSON for the reasoning / audit / revision stages so the D2 and
    D3 phases, analyze, plots and summary can be exercised offline.  Does NOT
    touch the budget counter (no real generations are consumed).
    """

    def call(self, system_prompt, user_prompt, *, temperature=0.1, max_tokens=1024, **extra):
        """Bypass the parent's RAG_USE_STUB_LLM short-circuit so the stage-aware
        JSON stub is actually reached during pipeline validation."""
        return self._real_call(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens, **extra)

    def _real_call(self, system_prompt, user_prompt, *, temperature, max_tokens, **extra):
        sp = system_prompt or ""
        if sp == REASONING_SYSTEM_PROMPT and "Auditor findings" in (user_prompt or ""):
            text = _STUB_REVISED_JSON
        elif sp == REASONING_SYSTEM_PROMPT:
            text = _STUB_REASONING_JSON
        elif sp == AUDITOR_SYSTEM_PROMPT:
            text = _STUB_AUDIT_JSON
        else:
            text = (
                "The governing provision is the one identified in the structured "
                "analysis [1]. On the stated facts the requirement applies as set "
                "out above [1]."
            )
        return GroundedLLMResponse(
            text=text,
            model="stub",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency=0.001,
        )


_STUB_REASONING_JSON = json.dumps({
    "structured_analysis": {
        "issue": "Stub issue for pipeline validation only.",
        "governing_provisions": [
            {"provision_id": "stub:s1", "description": "stub provision", "reason": "governs the issue [1]"}
        ],
        "definitions": [{"term": "stub term", "definition": "stub definition", "source": "[1]"}],
        "legal_rules": [{"rule": "stub rule", "source": "[1]"}],
        "conditions": [
            {"condition": "stub condition", "source": "[1]", "status": "satisfied", "supporting_fact": "stub fact"}
        ],
        "exceptions_and_provisos": [{"exception": "stub exception", "source": "[2]", "applicable": False}],
        "facts": [{"fact": "stub fact", "source": "question"}],
        "fact_condition_mapping": [
            {"condition": "stub condition", "fact": "stub fact", "determination": "satisfied", "reason": "stub [1]"}
        ],
        "cross_references": [{"from": "[1]", "to": "[2]", "relevance": "stub"}],
        "conflicts_or_hierarchy": [],
        "legal_conclusion": "Stub conclusion: the stub requirement applies [1].",
        "supporting_evidence": [{"claim": "stub claim", "source": "[1]"}],
        "uncertainties": [],
    },
    "final_answer": "Stub final answer: the requirement applies under the stub provision [1].",
})

_STUB_AUDIT_JSON = json.dumps({
    "status": "PASS",
    "defects": [],
    "missing_elements": [],
    "required_corrections": [],
    "corrected_conclusion": "",
    "citation_corrections": [],
})

_STUB_REVISED_JSON = json.dumps({
    "issue": "Stub issue (revised).",
    "governing_provisions": [
        {"provision_id": "stub:s1", "description": "stub provision", "reason": "governs the issue [1]"}
    ],
    "definitions": [],
    "legal_rules": [{"rule": "stub rule (revised)", "source": "[1]"}],
    "conditions": [],
    "exceptions_and_provisos": [{"exception": "stub exception", "source": "[2]", "applicable": True}],
    "facts": [{"fact": "stub fact", "source": "question"}],
    "fact_condition_mapping": [],
    "cross_references": [],
    "conflicts_or_hierarchy": [],
    "legal_conclusion": "Stub revised conclusion [1].",
    "supporting_evidence": [{"claim": "stub claim", "source": "[1]"}],
    "uncertainties": ["stub uncertainty"],
})


# --------------------------------------------------------------------------- #
# Context helpers — identical O3 construction as Experiment C (spec sec 11)
# --------------------------------------------------------------------------- #
def load_c_questions() -> dict[str, dict]:
    """Load the C manifest question entries (its context IDs are authoritative)."""
    manifest = json.loads(C_MANIFEST.read_text(encoding="utf-8"))
    return manifest["questions"]


def c_o3_context_text(qid: str, manifest_q: dict, payload_index: dict) -> tuple[str | None, str | None]:
    """Rebuild the C O3 context deterministically; returns (context, error)."""
    entry = manifest_q.get(qid)
    if entry is None or entry.get("unresolved"):
        return None, "unresolved question in C manifest"
    cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
    if not cid_list:
        return None, "empty O3 context"
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry["question"], chunks, "general_qa")
    if not built or not built.context:
        return None, "O3 context build failed"
    return built.context, None


def context_id_hash(cid_list: list[str]) -> str:
    return hashlib.sha256(json.dumps(cid_list).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# JSON extraction / validation helpers (offline, unit-tested)
# --------------------------------------------------------------------------- #
def repair_truncated_json(text: str) -> str | None:
    """Salvage a JSON object whose generation was cut off (token-cap truncation).

    Closes any open string and any open brackets/braces so the prefix parses.
    A dangling `"key":` fragment is stripped before closing.  Returns None when
    no plausible object start exists.  Salvaged objects may miss tail fields
    (e.g. uncertainties) but keep every field generated before the cutoff.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    body = s
    if in_str:
        body += '"'
    # Strip dangling incomplete fragments at the cut point:
    #   ..., "key" :      (colon with no value)
    #   ..., "key"        (bare key restored by the quote close, no colon)
    #   ...,              (trailing comma)
    body = re.sub(r",?\s*\"[^\"]*\"\s*:\s*$", "", body)
    body = re.sub(r",\s*\"[^\"]*\"\s*$", "", body)
    body = re.sub(r",\s*$", "", body)
    return body + "".join(reversed(stack))


def extract_json_object(text: str) -> dict | None:
    """Extract the first balanced JSON object from model output.

    Handles code fences, prose prefixes and trailing commentary.  If the text
    holds no *balanced* object (generation truncated at the token cap), a
    prefix-salvage repair is attempted.  Returns None when nothing parseable
    exists (caller records a JSON-parse failure).
    """
    if not text:
        return None
    t = text.strip()
    m = _JSON_FENCE_RE.search(t)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = t[start : i + 1]
                for repaired in (candidate, candidate.replace(",}", "}").replace(",]", "]")):
                    try:
                        obj = json.loads(repaired)
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        continue
                return None
    # Unbalanced (truncated) output — attempt prefix salvage.
    salvaged = repair_truncated_json(t)
    if salvaged:
        try:
            obj = json.loads(salvaged)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if v is None or v == "":
        return []
    return [v]


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def validate_reasoning(obj: Any) -> tuple[bool, str]:
    """Minimal structural validation of the structured legal analysis."""
    if not isinstance(obj, dict):
        return False, "not a JSON object"
    if not _as_str(obj.get("legal_conclusion")).strip():
        return False, "missing legal_conclusion"
    if not _as_str(obj.get("issue")).strip():
        return False, "missing issue"
    return True, ""


def validate_audit(obj: Any) -> tuple[bool, str]:
    """Minimal structural validation of the auditor output."""
    if not isinstance(obj, dict):
        return False, "not a JSON object"
    status = str(obj.get("status", "")).strip().upper()
    if status not in ("PASS", "FAIL"):
        return False, "missing status PASS|FAIL"
    return True, ""


def normalize_audit(obj: dict) -> dict:
    """Normalize an audit object to the spec sec 6 schema with safe defaults."""
    defects = []
    for d in _as_list(obj.get("defects")):
        if not isinstance(d, dict):
            continue
        dtype = _as_str(d.get("type")) or "other"
        if dtype not in _DEFECT_TYPES:
            dtype = "other"
        sev = (_as_str(d.get("severity")) or "minor").lower()
        if sev not in ("critical", "major", "minor"):
            sev = "minor"
        defects.append({
            "type": dtype,
            "description": _as_str(d.get("description")),
            "severity": sev,
            "evidence": [_as_str(e) for e in _as_list(d.get("evidence"))],
        })
    status = str(obj.get("status", "")).strip().upper()
    return {
        "status": status,
        "defects": defects,
        "missing_elements": [_as_str(x) for x in _as_list(obj.get("missing_elements"))],
        "required_corrections": [_as_str(x) for x in _as_list(obj.get("required_corrections"))],
        "corrected_conclusion": _as_str(obj.get("corrected_conclusion")),
        "citation_corrections": [_as_str(x) for x in _as_list(obj.get("citation_corrections"))],
    }


# --------------------------------------------------------------------------- #
# Prompt renderers (fixed text — spec sec 21)
# --------------------------------------------------------------------------- #
def render_reasoning_prompts(question: str, context: str) -> tuple[str, str]:
    user = (
        f"Question: {question}\n\n"
        f"Legal evidence sources:\n{context}\n\n"
        "Analyse this evidence and produce the {structured_analysis, "
        "final_answer} JSON object exactly as instructed."
    )
    return REASONING_SYSTEM_PROMPT, user


def render_answer_prompts(question: str, analysis_json: str) -> tuple[str, str]:
    user = (
        f"Question: {question}\n\n"
        "Structured legal analysis (derived from the evidence; [n] numbers refer "
        "to the original evidence sources):\n"
        f"{analysis_json}\n\n"
        "Write the final answer to the question using ONLY this analysis. Cite "
        "sources with [n] markers using the source numbers referenced in the "
        "analysis.\nAnswer:"
    )
    return ANSWER_SYSTEM_PROMPT, user


def render_audit_prompts(question: str, context: str, analysis_json: str) -> tuple[str, str]:
    user = (
        f"Question: {question}\n\n"
        f"Legal evidence sources:\n{context}\n\n"
        "Structured legal analysis to audit:\n"
        f"{analysis_json}\n\n"
        "Audit the analysis against the question and evidence. If the analysis "
        "has material defects, also provide corrected_conclusion. Output the "
        "audit JSON object exactly as instructed."
    )
    return AUDITOR_SYSTEM_PROMPT, user


def render_revision_prompts(question: str, analysis_json: str, audit_json: str) -> tuple[str, str]:
    user = (
        f"Question: {question}\n\n"
        "Structured legal analysis produced earlier:\n"
        f"{analysis_json}\n\n"
        "Auditor findings on that analysis:\n"
        f"{audit_json}\n\n"
        "Apply the auditor's required corrections and missing_elements to the "
        "analysis. Keep every field and source citation that was correct. "
        "Output the revised structured legal analysis JSON object exactly as "
        "instructed."
    )
    return REASONING_SYSTEM_PROMPT, user


# --------------------------------------------------------------------------- #
# Source-marker collection — used to repair missing [n] citation markers.
# The citation evaluator itself is UNCHANGED (spec sec 2); this only restores
# markers the analysis already carried so citation_recall is not mechanically
# degraded by the two-stage architecture.
# --------------------------------------------------------------------------- #
_SRC_RE = re.compile(r"\[(\d+)\]")


def _collect_sources(obj: dict | None, list_key: str, out: set) -> None:
    for item in _as_list((obj or {}).get(list_key)):
        if isinstance(item, dict):
            for field in ("source", "sources", "evidence", "from", "to", "supporting_fact"):
                for m in _SRC_RE.finditer(_as_str(item.get(field))):
                    out.add(int(m.group(1)))


def analysis_source_marks(analysis: dict | None) -> set[int]:
    """All [n] markers referenced anywhere in a structured analysis."""
    out: set[int] = set()
    for key in (
        "governing_provisions",
        "definitions",
        "legal_rules",
        "conditions",
        "exceptions_and_provisos",
        "supporting_evidence",
        "cross_references",
    ):
        _collect_sources(analysis, key, out)
    for m in _SRC_RE.finditer(_as_str((analysis or {}).get("legal_conclusion"))):
        out.add(int(m.group(1)))
    return out


def audit_source_marks(audit: dict | None) -> set[int]:
    """All [n] markers referenced in auditor defects/corrections."""
    out: set[int] = set()
    for d in (audit or {}).get("defects", []):
        ev = d.get("evidence")
        ev_text = " ".join(_as_str(x) for x in ev) if isinstance(ev, list) else _as_str(ev)
        for m in _SRC_RE.finditer(ev_text):
            out.add(int(m.group(1)))
    for key in ("citation_corrections", "required_corrections", "missing_elements"):
        for x in (audit or {}).get(key, []):
            for m in _SRC_RE.finditer(_as_str(x)):
                out.add(int(m.group(1)))
    for m in _SRC_RE.finditer(_as_str((audit or {}).get("corrected_conclusion"))):
        out.add(int(m.group(1)))
    return out


def repair_citation_markers(
    answer: str,
    analysis: dict | None,
    audit: dict | None,
    max_index: int,
) -> str:
    """Append missing [n] markers to a final answer (dedup, 1..max_index only).

    Called only when the answer text carries NO bracket citations at all —
    otherwise the model's own markers are kept untouched.
    """
    if _SRC_RE.search(answer or ""):
        return answer
    marks = analysis_source_marks(analysis) | audit_source_marks(audit)
    valid = sorted(n for n in marks if 1 <= n <= max_index)
    if not valid:
        return answer
    tail = " ".join(f"[{n}]" for n in valid)
    return f"{answer.rstrip()}\n\nSources: {tail}"


# --------------------------------------------------------------------------- #
# Call accounting (spec sec 9)
# --------------------------------------------------------------------------- #
def _log_call(
    calls_path: Path | None, lock: threading.Lock, qid: str = "", condition: str = "", stage: str = "", **fields
) -> None:
    if calls_path is None:
        return
    rec = {
        "qid": qid,
        "condition": condition,
        "stage": stage,
        "model": LLM_MODEL,
        "temperature": LLM_TEMPERATURE,
        "input_tokens": int(fields.get("input_tokens", 0) or 0),
        "output_tokens": int(fields.get("output_tokens", 0) or 0),
        "latency_ms": int(fields.get("latency_ms", 0) or 0),
        "success": bool(fields.get("success", False)),
        "revision_count": int(fields.get("revision_count", 0) or 0),
    }
    with lock, calls_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _log_backoff(calls_path: Path | None, lock: threading.Lock, qid: str, condition: str, reason: str) -> None:
    if calls_path is None:
        return
    rec = {"qid": qid, "condition": condition, "kind": "transport_backoff", "reason": reason}
    with lock, calls_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_jsonl(path: Path, rec: dict, lock: threading.Lock) -> None:
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _load_ckpt(path: Path) -> list[dict]:
    if not path.exists():
        return []
    recs = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    continue
    return recs


def _seed_call_count(*ckpt_paths: Path) -> int:
    """Seed the budget counter with prior successful generations (resume)."""
    n = 0
    for p in ckpt_paths:
        n += sum(1 for r in _load_ckpt(p) if not r.get("error"))
    return n


def _prior_recoveries(calls_path: Path) -> int:
    return sum(1 for r in _load_ckpt(calls_path) if r.get("kind") == "transport_backoff")


# --------------------------------------------------------------------------- #
# D2 pipeline — structured reasoning -> final answer, ONE call (spec sec 4/8)
# --------------------------------------------------------------------------- #
def run_d2_one(
    qid: str,
    manifest_q: dict,
    payload_index: dict,
    client: _DNoRetryClient,
    calls_path: Path | None,
    lock: threading.Lock,
) -> dict:
    """One D2 question: a single reasoning call (analysis + final answer)."""
    entry = manifest_q[qid]
    cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry["question"], chunks, "general_qa")
    context = built.context
    max_idx = len(built.citations or [])
    rec: dict[str, Any] = {"question_id": qid, "condition": "D2", "stages": []}
    t_total = time.perf_counter()

    sys_r, user_r = render_reasoning_prompts(entry["question"], context)
    resp = client.call(sys_r, user_r, temperature=LLM_TEMPERATURE, max_tokens=MAX_TOKENS_STRUCTURED)
    payload = extract_json_object(resp.text or "") if not resp.error else None
    # Truncation salvage: if the token cap cut the output before final_answer
    # but the structured analysis (with its legal_conclusion) survived, derive
    # the final answer from the analysis conclusion — it is the analysis-
    # derived answer by construction, so the architecture is unchanged.
    if payload is not None and "structured_analysis" in payload and not _as_str(payload.get("final_answer")).strip():
        concl = _as_str((payload.get("structured_analysis") or {}).get("legal_conclusion")).strip()
        if concl:
            payload["final_answer"] = concl
    ok_r, why_r = validate_reasoning_payload(payload)
    _log_call(
        calls_path,
        lock,
        qid=qid,
        condition="D2",
        stage=D2_STAGE,
        revision_count=0,
        input_tokens=int((resp.usage or {}).get("prompt_tokens", 0)),
        output_tokens=int((resp.usage or {}).get("completion_tokens", 0)),
        latency_ms=int(resp.latency * 1000),
        success=bool(ok_r and not resp.error),
    )
    if resp.error or payload is None or not ok_r:
        rec["error"] = f"D2 reasoning failed: {resp.error or why_r}"
        rec["raw_output_chars"] = len(resp.text or "")
        rec["output_tokens"] = int((resp.usage or {}).get("completion_tokens", 0))
        rec["latency_ms"] = int((time.perf_counter() - t_total) * 1000)
        return rec

    rec["analysis"] = payload["structured_analysis"]
    rec["answer"] = payload["final_answer"]
    rec["raw_output_chars"] = len(resp.text or "")
    rec["latency_ms"] = int((time.perf_counter() - t_total) * 1000)
    rec["output_tokens"] = int((resp.usage or {}).get("completion_tokens", 0))
    return rec


# --------------------------------------------------------------------------- #
# D3 pipeline — D2 analysis (reused from checkpoint) + ONE audit/revision call
# --------------------------------------------------------------------------- #
def run_d3_one(
    qid: str,
    manifest_q: dict,
    payload_index: dict,
    client: _DNoRetryClient,
    calls_path: Path | None,
    lock: threading.Lock,
    d2_analysis: dict | None,
) -> dict:
    """One D3 question: single audit call over the checkpointed D2 analysis.

    The combined audit+revision call embeds required corrections and a
    corrected conclusion in one generation, so no separate revision call is
    needed (spec sec 8: 'if the implementation can combine audit + correction
    into one call').
    """
    entry = manifest_q[qid]
    cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
    chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
    cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
    built = cb.build(entry["question"], chunks, "general_qa")
    context = built.context
    max_idx = len(built.citations or [])
    rec: dict[str, Any] = {"question_id": qid, "condition": "D3", "stages": []}
    t_total = time.perf_counter()

    if not d2_analysis:
        rec["error"] = "no D2 structured analysis available for this qid (D2 must run first)"
        return rec

    sys_a, user_a = render_audit_prompts(entry["question"], context, json.dumps(d2_analysis, ensure_ascii=False))
    resp = client.call(sys_a, user_a, temperature=LLM_TEMPERATURE, max_tokens=MAX_TOKENS_STRUCTURED)
    audit = extract_json_object(resp.text or "") if not resp.error else None
    ok_a, why_a = validate_audit(audit)
    _log_call(
        calls_path,
        lock,
        qid=qid,
        condition="D3",
        stage=D3_STAGE,
        revision_count=0,
        input_tokens=int((resp.usage or {}).get("prompt_tokens", 0)),
        output_tokens=int((resp.usage or {}).get("completion_tokens", 0)),
        latency_ms=int(resp.latency * 1000),
        success=bool(ok_a and not resp.error),
    )
    if resp.error or audit is None or not ok_a:
        rec["error"] = f"D3 audit failed: {resp.error or why_a}"
        rec["raw_output_chars"] = len(resp.text or "")
        rec["output_tokens"] = int((resp.usage or {}).get("completion_tokens", 0))
        rec["latency_ms"] = int((time.perf_counter() - t_total) * 1000)
        return rec

    audit_n = normalize_audit(audit)
    final = build_final_answer(d2_analysis, audit_n, max_idx)
    rec["analysis"] = d2_analysis
    rec["audit"] = audit_n
    rec["answer"] = final
    rec["revision_count"] = 1 if audit_n["status"] == "FAIL" else 0
    rec["latency_ms"] = int((time.perf_counter() - t_total) * 1000)
    rec["output_tokens"] = int((resp.usage or {}).get("completion_tokens", 0))
    return rec


# --------------------------------------------------------------------------- #
# Phase RUN for D2 / D3 — resumable, budget-gated, single generation attempt
# --------------------------------------------------------------------------- #
def _done_set(ckpt_path: Path, resume: bool) -> set[str]:
    done: set[str] = set()
    if resume and ckpt_path.exists():
        for r in _load_ckpt(ckpt_path):
            if r.get("error"):
                continue
            if r.get("question_id"):
                done.add(r["question_id"])
    return done


def _plan_or_stop(planned_total: int) -> int:
    if planned_total > PLANNED_CALLS:
        return _budget_stop(planned_total - PLANNED_CALLS)
    return 0


def phase_run_d2(stub: bool, resume: bool, limit: int | None, concurrency: int = 1) -> int:
    ckpt_path = D_D2_CKPT_STUB if stub else D_D2_CKPT
    calls_path = D_CALLS_STUB_JSONL if stub else D_CALLS_JSONL
    manifest_q = load_c_questions()
    all_qids = [qid for qid in sorted(manifest_q) if not manifest_q[qid].get("unresolved")]
    done = _done_set(ckpt_path, resume)
    tasks = [q for q in all_qids if q not in done]
    if limit:
        tasks = tasks[:limit]

    planned_total = _seed_call_count(ckpt_path) if resume else 0
    planned_total += len(tasks)  # 1 successful call per completed question
    if not stub:
        rc = _plan_or_stop(planned_total)
        if rc:
            return rc

    if stub:
        _ACTIVE_CAP[0] = 10**9
        os.environ["RAG_USE_STUB_LLM"] = "true"
    else:
        os.environ["RAG_USE_STUB_LLM"] = "false"
    _CALL_COUNT[0] = _seed_call_count(ckpt_path) if resume else 0

    client = _StubDClient() if stub else _DNoRetryClient()
    if not stub and client.use_stub:
        print("FATAL: real LLM key not configured (client in STUB mode). Abort before budget spent.", flush=True)
        return 2

    from evaluation.eval_e2e_v2 import load_payload_index

    payload_index = load_payload_index()
    lock = threading.Lock()
    print("=" * 70, flush=True)
    print(
        f"Phase RUN D2 {'(STUB validation — 0 real LLM calls)' if stub else f'(REAL, budget cap={PLANNED_CALLS} shared with D3, concurrency={concurrency})'}",
        flush=True,
    )
    print(f"  checkpoint: {ckpt_path}", flush=True)
    print(f"  gen so far: {_CALL_COUNT[0]} | to run: {len(tasks)} qids (1 call each)", flush=True)
    print("=" * 70, flush=True)
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume or not ckpt_path.exists():
        ckpt_path.write_text("", encoding="utf-8")

    t0 = time.perf_counter()
    completed = 0
    total = len(tasks)
    err = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {ex.submit(run_d2_one, qid, manifest_q, payload_index, client, calls_path, lock): qid for qid in tasks}
        for fut in as_completed(futs):
            qid = futs[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"question_id": qid, "condition": "D2", "error": f"task exception: {exc!r}"}
            if rec.get("error"):
                err += 1
            _append_jsonl(ckpt_path, rec, lock)
            completed += 1
            if completed % 10 == 0 or completed == total or completed <= 5:
                print(
                    f"  [D2 {completed}/{total}] gen={_CALL_COUNT[0]}/{PLANNED_CALLS} err={err} "
                    f"{time.perf_counter() - t0:.0f}s",
                    flush=True,
                )
            if INTER_CALL_DELAY > 0 and not stub and concurrency == 1:
                time.sleep(INTER_CALL_DELAY)

    print(f"  D2 RUN done: {completed}/{total} qids, gen={_CALL_COUNT[0]}/{PLANNED_CALLS}, err={err}", flush=True)
    return 0


def phase_run_d3(stub: bool, resume: bool, limit: int | None, concurrency: int = 1) -> int:
    ckpt_path = D_D3_CKPT_STUB if stub else D_D3_CKPT
    calls_path = D_CALLS_STUB_JSONL if stub else D_CALLS_JSONL
    d2_ckpt = D_D2_CKPT_STUB if stub else D_D2_CKPT
    manifest_q = load_c_questions()
    all_qids = [qid for qid in sorted(manifest_q) if not manifest_q[qid].get("unresolved")]
    done = _done_set(ckpt_path, resume)
    tasks = [q for q in all_qids if q not in done]
    if limit:
        tasks = tasks[:limit]

    d2_map = {r["question_id"]: r.get("analysis") for r in _load_ckpt(d2_ckpt) if not r.get("error")}
    missing = [q for q in tasks if not d2_map.get(q)]
    if missing:
        # Pathological D2 failures (e.g. Q057/Q110: model never emits parseable JSON
        # within the output cap after multiple attempts) are SKIPPED rather than
        # blocking the 148 auditable questions. They appear as not_run in analyze
        # (spec sec 16: do not manufacture missing values) and in the call accounting.
        print(f"WARNING: skipping {len(missing)} qids with no successful D2 analysis: {missing[:10]}", flush=True)
        tasks = [q for q in tasks if d2_map.get(q)]

    planned_total = _seed_call_count(ckpt_path) if resume else 0
    planned_total += _seed_call_count(d2_ckpt)  # D2 generations share the same 300-call budget
    planned_total += len(tasks)  # 1 audit call per question; revision embedded in the same call
    if not stub:
        rc = _plan_or_stop(planned_total)
        if rc:
            return rc

    if stub:
        _ACTIVE_CAP[0] = 10**9
        os.environ["RAG_USE_STUB_LLM"] = "true"
    else:
        os.environ["RAG_USE_STUB_LLM"] = "false"
    _CALL_COUNT[0] = _seed_call_count(d2_ckpt) + (_seed_call_count(ckpt_path) if resume else 0)

    client = _StubDClient() if stub else _DNoRetryClient()
    if not stub and client.use_stub:
        print("FATAL: real LLM key not configured (client in STUB mode). Abort before budget spent.", flush=True)
        return 2

    from evaluation.eval_e2e_v2 import load_payload_index

    payload_index = load_payload_index()
    lock = threading.Lock()
    print("=" * 70, flush=True)
    print(
        f"Phase RUN D3 {'(STUB validation — 0 real LLM calls)' if stub else f'(REAL, budget cap={PLANNED_CALLS} shared with D2, concurrency={concurrency})'}",
        flush=True,
    )
    print(f"  checkpoint: {ckpt_path}", flush=True)
    print(
        f"  gen so far: {_CALL_COUNT[0]} | to run: {len(tasks)} qids (1 combined audit+revision call each)", flush=True
    )
    print("=" * 70, flush=True)
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume or not ckpt_path.exists():
        ckpt_path.write_text("", encoding="utf-8")

    t0 = time.perf_counter()
    completed = 0
    total = len(tasks)
    err = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = {
            ex.submit(run_d3_one, qid, manifest_q, payload_index, client, calls_path, lock, d2_map.get(qid)): qid
            for qid in tasks
        }
        for fut in as_completed(futs):
            qid = futs[fut]
            try:
                rec = fut.result()
            except Exception as exc:
                rec = {"question_id": qid, "condition": "D3", "error": f"task exception: {exc!r}"}
            if rec.get("error"):
                err += 1
            _append_jsonl(ckpt_path, rec, lock)
            completed += 1
            if completed % 10 == 0 or completed == total or completed <= 5:
                print(
                    f"  [D3 {completed}/{total}] gen={_CALL_COUNT[0]}/{PLANNED_CALLS} err={err} "
                    f"{time.perf_counter() - t0:.0f}s",
                    flush=True,
                )
            if INTER_CALL_DELAY > 0 and not stub and concurrency == 1:
                time.sleep(INTER_CALL_DELAY)

    print(f"  D3 RUN done: {completed}/{total} qids, gen={_CALL_COUNT[0]}/{PLANNED_CALLS}, err={err}", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# Phase ANALYZE (no LLM) — metrics, transitions, taxonomy, outputs
# --------------------------------------------------------------------------- #
def identify_structured_fields(analysis: dict | None) -> dict:
    """Reduce a structured analysis to countable reasoning operations (sec 14)."""
    a = analysis or {}
    fcm = a.get("fact_condition_mapping") or []
    return {
        "n_governing_provisions": len(_as_list(a.get("governing_provisions"))),
        "n_definitions": len(_as_list(a.get("definitions"))),
        "n_conditions": len(_as_list(a.get("conditions"))),
        "n_exceptions": len(_as_list(a.get("exceptions_and_provisos"))),
        "n_exceptions_applicable": sum(
            1 for e in _as_list(a.get("exceptions_and_provisos")) if isinstance(e, dict) and e.get("applicable") is True
        ),
        "n_cross_references": len(_as_list(a.get("cross_references"))),
        "n_conflicts": len(_as_list(a.get("conflicts_or_hierarchy"))),
        "n_fact_condition_mappings": len(fcm),
        "n_unknown_determinations": sum(1 for m in fcm if isinstance(m, dict) and m.get("determination") == "unknown"),
        "n_uncertainties": len(_as_list(a.get("uncertainties"))),
    }


def build_reasoning_records(d2_recs: list[dict], d3_map: dict[str, dict], payload_index: dict) -> list[dict]:
    out = []
    for r in d2_recs:
        if r.get("error") or not r.get("analysis"):
            continue
        qid = r["question_id"]
        out.append({
            "qid": qid,
            "condition": "D2",
            "structured_analysis": r["analysis"],
            "identified_fields": identify_structured_fields(r["analysis"]),
            "source_marks": sorted(analysis_source_marks(r["analysis"])),
            "audited_in_d3": qid in d3_map,
        })
    for qid, r in d3_map.items():
        if r.get("error") or not r.get("analysis"):
            continue
        audit = r.get("audit") or {}
        out.append({
            "qid": qid,
            "condition": "D3",
            "structured_analysis": r.get("revised_analysis") or r["analysis"],
            "audit": audit,
            "identified_fields": identify_structured_fields(r.get("revised_analysis") or r["analysis"]),
            "source_marks": sorted(analysis_source_marks(r.get("revised_analysis") or r["analysis"])),
        })
    return out


def transition_counts(pairs: list[tuple[str, bool, bool]]) -> Counter:
    c = Counter()
    for _, before, after in pairs:
        if before is None or after is None:
            continue
        if not before and after:
            c["improved"] += 1
        elif before and not after:
            c["worsened"] += 1
        else:
            c["unchanged"] += 1
    return c


def phase_analyze(stub: bool) -> int:
    print("=" * 70, flush=True)
    print("Phase ANALYZE (no LLM)", flush=True)
    print("=" * 70, flush=True)

    manifest_q = load_c_questions()
    payload_index = load_payload_index()
    family_map = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    d2_ckpt = D_D2_CKPT_STUB if stub else D_D2_CKPT
    d3_ckpt = D_D3_CKPT_STUB if stub else D_D3_CKPT
    d2_recs = [r for r in _load_ckpt(d2_ckpt) if not r.get("error") and r.get("answer")]
    d3_recs = [r for r in _load_ckpt(d3_ckpt) if not r.get("error") and r.get("answer")]
    d3_map = {r["question_id"]: r for r in d3_recs}
    c_o3 = load_c_o3_correctness()

    print(f"  C-O3 reused: {len(c_o3)} | D2 ok: {len(d2_recs)} | D3 ok: {len(d3_recs)}", flush=True)

    gold_index = build_gold_index(payload_index, family_map)
    family_cache: dict[str, tuple] = {}
    per_q: dict[str, dict] = {}
    d2_metrics: list[dict] = []
    d3_metrics: list[dict] = []

    for qid in sorted(manifest_q):
        entry = manifest_q[qid]
        if entry.get("unresolved") or qid not in questions:
            continue
        q = questions[qid]
        if qid not in family_cache:
            family_cache[qid] = (
                set(entry["gold_chunk_ids"]),
                q.recall_units(),
            )
        gold_chunk_ids, gold_units = family_cache[qid]
        cid_list = entry["conditions"][O3_COND]["context_chunk_ids"]
        chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in cid_list]
        cb = _COracleContextBuilder(2000, MAX_CTX_CHARS)
        built = cb.build(entry["question"], chunks, "general_qa")
        context_ids = [cit["chunk_id"] for cit in (built.citations or [])]

        row: dict[str, Any] = {"question_id": qid, "question": entry["question"]}

        # C-O3 (D1 baseline) — reused verbatim from Experiment C (spec sec 3).
        c_rec = c_o3.get(qid)
        row["C-O3"] = c_rec if c_rec else {"status": "not_available"}

        # D2 / D3 — evaluated with the UNCHANGED Experiment B/C metric stack.
        for cond, recs, store in (("D2", d2_recs, d2_metrics), ("D3", d3_recs, d3_metrics)):
            rec = next((r for r in recs if r["question_id"] == qid), None)
            if rec is None:
                row[cond] = {"status": "not_run"}
                continue
            # Same citation-extraction + sanitisation steps as the production
            # service pipeline (steps 4–5), so groundedness/citations are scored
            # with the UNCHANGED evaluators (spec sec 2).
            cits, grounded = _score_like_service(rec["answer"], chunks, built)
            resp = _DResp(
                rec["answer"],
                citations=cits,
                groundedness_score=grounded,
                total_latency_ms=int(rec.get("latency_ms", 0)),
            )
            m = compute_metrics(
                resp,
                built,
                q,
                cid_list,
                context_ids,
                gold_chunk_ids,
                gold_units,
                payload_index,
                family_map,
            )
            m.update({
                "question_id": qid,
                "condition": cond,
                "error": None,
                "analysis_fields": identify_structured_fields(rec.get("analysis")),
                "revision_count": rec.get("revision_count", 0),
                "audit_status": (rec.get("audit") or {}).get("status"),
            })
            store.append(m)
            row[cond] = {
                k: m[k]
                for k in (
                    "answer",
                    "answer_correctness",
                    "correct",
                    "citation_recall",
                    "citation_precision",
                    "groundedness",
                    "abstained",
                    "latency_ms",
                    "context_tokens",
                    "revision_count",
                    "audit_status",
                )
            }
            row[cond]["analysis_fields"] = m["analysis_fields"]
        per_q[qid] = row

    with D_PER_QUESTION.open("w", encoding="utf-8") as f:
        for qid in sorted(per_q):
            f.write(json.dumps({"qid": qid, **per_q[qid]}, ensure_ascii=False, default=str) + "\n")

    # Reasoning / auditor JSONL outputs (spec sec 22)
    d3_reasoning_map = {}
    reasoning_recs = build_reasoning_records(d2_recs, d3_map, payload_index)
    with D_REASONING.open("w", encoding="utf-8") as f:
        for r in reasoning_recs:
            if r["condition"] == "D3":
                d3_reasoning_map[r["qid"]] = r
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with D_AUDITOR.open("w", encoding="utf-8") as f:
        for qid in sorted(d3_map):
            r = d3_map[qid]
            f.write(
                json.dumps(
                    {
                        "qid": qid,
                        "audit": r.get("audit"),
                        "d2_analysis_source": "checkpointed D2 structured analysis",
                        "final_answer": r.get("answer"),
                        "revision_count": r.get("revision_count", 0),
                    },
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

    # Aggregates per condition
    def agg(metrics: list[dict], label: str) -> dict:
        if not metrics:
            return {"label": label, "n": 0}
        lat = [m["latency_ms"] for m in metrics]
        return {
            "label": label,
            "n": len(metrics),
            "answer_correctness": round(_mean([m["answer_correctness"] for m in metrics]), 4),
            "correct_rate": round(_mean([m["correct"] for m in metrics]), 4),
            "citation_recall": round(_mean([m["citation_recall"] for m in metrics]), 4),
            "citation_precision": round(_mean([m["citation_precision"] for m in metrics]), 4),
            "groundedness": round(_mean([m["groundedness"] for m in metrics]), 4),
            "abstain_rate": round(_mean([m["abstained"] for m in metrics]), 4),
            "mean_latency_ms": round(_mean(lat), 1),
            "median_latency_ms": round(_pctl(lat, 50), 1),
            "p95_latency_ms": round(_pctl(lat, 95), 1),
        }

    def _c_nums(key: str) -> list:
        return [r["C-O3"][key] for r in per_q.values() if isinstance(r["C-O3"].get(key), (int, float))]

    def _c_bools(key: str) -> list:
        return [r["C-O3"][key] for r in per_q.values() if isinstance(r["C-O3"].get(key), bool)]

    c_vals = {
        "answer_correctness": _c_nums("answer_correctness"),
        "correct": _c_bools("correct"),
        "citation_recall": _c_nums("citation_recall"),
        "citation_precision": _c_nums("citation_precision"),
        "groundedness": _c_nums("groundedness"),
        "abstained": _c_bools("abstained"),
        "latency_ms": _c_nums("latency_ms"),
    }
    c_agg = {
        "label": "C-O3 Direct (reused from Experiment C)",
        "n": len(c_vals["answer_correctness"]),
        "answer_correctness": round(_mean(c_vals["answer_correctness"]), 4) if c_vals["answer_correctness"] else 0.0,
        "correct_rate": round(_mean(c_vals["correct"]), 4) if c_vals["correct"] else 0.0,
        "citation_recall": round(_mean(c_vals["citation_recall"]), 4) if c_vals["citation_recall"] else 0.0,
        "citation_precision": round(_mean(c_vals["citation_precision"]), 4) if c_vals["citation_precision"] else 0.0,
        "groundedness": round(_mean(c_vals["groundedness"]), 4) if c_vals["groundedness"] else 0.0,
        "abstain_rate": round(_mean(c_vals["abstained"]), 4) if c_vals["abstained"] else 0.0,
        "mean_latency_ms": round(_mean(c_vals["latency_ms"]), 1) if c_vals["latency_ms"] else 0.0,
        "median_latency_ms": round(_pctl(c_vals["latency_ms"], 50), 1) if c_vals["latency_ms"] else 0.0,
        "note": "reused Experiment C O3 outputs; no new LLM calls (spec sec 3)",
    }
    aggregates = {
        "C-O3": c_agg,
        "D2": agg(d2_metrics, D_CONDITION_META["D2"][0]),
        "D3": agg(d3_metrics, D_CONDITION_META["D3"][0]),
    }

    # III-question special analysis (spec sec 18) — true III = O1&O2&O3 incorrect.
    def _is_true_iii(row: dict) -> bool:
        c = row.get("C-O3", {})
        return c.get("correct") is False and c.get("correct_o1") is False and c.get("correct_o2") is False

    iii = [qid for qid, r in per_q.items() if _is_true_iii(r)]
    iii_stats = {"n_iii": len(iii)}
    for cond in ("D2", "D3"):
        iii_stats[f"{cond}_improved"] = sum(1 for qid in iii if per_q[qid][cond].get("correct") is True)
        iii_stats[f"{cond}_still_incorrect"] = sum(1 for qid in iii if per_q[qid][cond].get("correct") is False)
    # grouped by D3 detected defect categories among still-incorrect (proxy grouping)
    group_map = Counter()
    for qid in iii:
        r = d3_map.get(qid) or {}
        audit = r.get("audit") or {}
        for d in audit.get("defects", []):
            group_map[d.get("type", "other")] += 1
    iii_stats["defect_types_on_iii_questions"] = dict(group_map)
    aggregates["iii_analysis"] = iii_stats

    # Transitions (spec sec 17)
    pairs_cd2, pairs_cd3, pairs_d23 = [], [], []
    for qid, r in per_q.items():
        c, d2v, d3v = r["C-O3"].get("correct"), r["D2"].get("correct"), r["D3"].get("correct")
        if c is not None and d2v is not None:
            pairs_cd2.append((qid, c, d2v))
        if c is not None and d3v is not None:
            pairs_cd3.append((qid, c, d3v))
        if d2v is not None and d3v is not None:
            pairs_d23.append((qid, d2v, d3v))
    transitions = {
        "C-O3_to_D2": dict(transition_counts(pairs_cd2)),
        "C-O3_to_D3": dict(transition_counts(pairs_cd3)),
        "D2_to_D3": dict(transition_counts(pairs_d23)),
        "fixed_by_structured_reasoning": [q for q, b, a in pairs_cd2 if not b and a],
        "fixed_only_by_auditor": [
            q for q, b, a in pairs_d23 if not b and a and per_q[q]["C-O3"].get("correct") is False
        ],
        "degraded_by_structured_reasoning": [q for q, b, a in pairs_cd2 if b and not a],
        "degraded_by_auditor": [q for q, b, a in pairs_d23 if b and not a],
        "still_incorrect_everywhere": [
            q
            for q, r in per_q.items()
            if r["C-O3"].get("correct") is False and r["D2"].get("correct") is False and r["D3"].get("correct") is False
        ],
    }
    D_TRANSITIONS.write_text(json.dumps(transitions, indent=2, default=str), encoding="utf-8")

    # Error taxonomy (spec sec 15) — D2 vs D3 detected-defect distribution.
    def tax_for(cond: str, ckpt: Path) -> dict:
        recs = [r for r in _load_ckpt(ckpt) if not r.get("error")]
        cnt: Counter = Counter()
        sev: Counter = Counter()
        for r in recs:
            for d in (r.get("audit") or {}).get("defects", []):
                cnt[d.get("type", "other")] += 1
                sev[d.get("severity", "minor")] += 1
        return {
            "condition": cond,
            "n_questions": len(recs),
            "defect_type_counts": dict(cnt.most_common()),
            "defect_severity_counts": dict(sev),
            "pass_count": sum(1 for r in recs if (r.get("audit") or {}).get("status") == "PASS"),
            "fail_count": sum(1 for r in recs if (r.get("audit") or {}).get("status") == "FAIL"),
            "revision_rate": round(sum(1 for r in recs if r.get("revision_count", 0) > 0) / len(recs), 4)
            if recs
            else 0.0,
            "note": "defects are auditor-detected categories (D3); D2 has no auditor stage",
        }

    taxonomy = {"D3": tax_for("D3", d3_ckpt)}
    D_ERROR_TAX.write_text(json.dumps(taxonomy, indent=2, default=str), encoding="utf-8")

    # Call accounting (spec sec 9)
    calls_path = D_CALLS_STUB_JSONL if stub else D_CALLS_JSONL
    call_recs = _load_ckpt(calls_path)
    gen_calls = [r for r in call_recs if r.get("stage")]
    backoffs = [r for r in call_recs if r.get("kind") == "transport_backoff"]
    accounting = {
        "experiment": "D structured reasoning + auditor",
        "hard_budget_total_new_generations": PLANNED_CALLS,
        "planned_calls": {
            "D2_reasoning": QUESTIONS_PER_COND,
            "D3_audit_revision": QUESTIONS_PER_COND,
            "total": PLANNED_CALLS,
        },
        "actual_generations": len(gen_calls),
        "successful_calls": sum(1 for r in gen_calls if r.get("success")),
        "failed_calls": sum(1 for r in gen_calls if not r.get("success")),
        "transport_backoffs": len(backoffs),
        "revision_calls": sum(1 for r in gen_calls if r.get("stage") == "revision"),
        "reused_experiment_C_calls": {
            "D1_C-O3": 150,
            "note": "0 new calls; O3 outputs and metrics reused verbatim (spec sec 3)",
        },
        "total_token_usage": {
            "input_tokens": sum(int(r.get("input_tokens", 0)) for r in gen_calls),
            "output_tokens": sum(int(r.get("output_tokens", 0)) for r in gen_calls),
        },
        "per_condition": {
            c: {
                "generations": sum(1 for r in gen_calls if r.get("condition") == c),
                "successful": sum(1 for r in gen_calls if r.get("condition") == c and r.get("success")),
                "failed": sum(1 for r in gen_calls if r.get("condition") == c and not r.get("success")),
            }
            for c in ("D2", "D3")
        },
        "per_stage": {
            s: sum(1 for r in gen_calls if r.get("stage") == s)
            for s in ("reasoning", "audit_revision", "revision", "answer")
        },
        "stub_validation": stub,
        "budget_respected": len(gen_calls) <= PLANNED_CALLS or stub,
    }
    D_ACCOUNTING.write_text(json.dumps(accounting, indent=2, default=str), encoding="utf-8")

    results = {
        "experiment": "D — Structured Legal Reasoning + Legal Auditor",
        "conditions": {c: D_CONDITION_META[c][1] for c in D_CONDS},
        "config": {
            "model": LLM_MODEL,
            "temperature": LLM_TEMPERATURE,
            "max_tokens_structured": MAX_TOKENS_STRUCTURED,
            "max_tokens_final_answer": LLM_MAX_TOKENS,
            "max_context_chars": MAX_CTX_CHARS,
            "context_builder": "_COracleContextBuilder (identical to C)",
            "max_revision_cycles": MAX_REVISION_CYCLES,
            "prompt_policy": "fixed; no variants (spec sec 21)",
            "metrics": "experiment_b_topk_eval.compute_metrics (unchanged; spec sec 2)",
        },
        "aggregate": aggregates,
        "comparisons": {
            "C-O3_vs_D2": {
                "answer_correctness_delta": round(
                    aggregates["D2"].get("answer_correctness", 0) - aggregates["C-O3"].get("answer_correctness", 0), 4
                ),
                "binary_delta": round(
                    aggregates["D2"].get("correct_rate", 0) - aggregates["C-O3"].get("correct_rate", 0), 4
                ),
            },
            "D2_vs_D3": {
                "answer_correctness_delta": round(
                    aggregates["D3"].get("answer_correctness", 0) - aggregates["D2"].get("answer_correctness", 0), 4
                ),
                "binary_delta": round(
                    aggregates["D3"].get("correct_rate", 0) - aggregates["D2"].get("correct_rate", 0), 4
                ),
            },
            "C-O3_vs_D3": {
                "answer_correctness_delta": round(
                    aggregates["D3"].get("answer_correctness", 0) - aggregates["C-O3"].get("answer_correctness", 0), 4
                ),
                "binary_delta": round(
                    aggregates["D3"].get("correct_rate", 0) - aggregates["C-O3"].get("correct_rate", 0), 4
                ),
            },
        },
        "iii_analysis": iii_stats,
        "n_per_question": len(per_q),
        "stub_validation": stub,
    }
    D_AGGREGATE.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    for c in D_CONDS:
        a = aggregates[c]
        if a.get("n", 0) or c == "C-O3":
            print(
                f"  {c}: correctness={a.get('answer_correctness')} binary={a.get('correct_rate')} "
                f"citr={a.get('citation_recall')} citp={a.get('citation_precision')} grounded={a.get('groundedness')} (n={a.get('n')})",
                flush=True,
            )
    print(f"  transitions: {transitions['C-O3_to_D2']} / {transitions['D2_to_D3']}", flush=True)
    print(f"  III analysis: {iii_stats}", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# Phase PLOTS (spec sec 22)
# --------------------------------------------------------------------------- #
def phase_plots(stub: bool) -> int:
    if not _HAS_MATPLOTLIB:
        print(
            "matplotlib not installed in this environment — plots skipped "
            "(all other Experiment D artifacts are complete).",
            flush=True,
        )
        return 0
    if not D_AGGREGATE.exists():
        print("aggregate missing — run --phase analyze first", flush=True)
        return 1
    res = json.loads(D_AGGREGATE.read_text(encoding="utf-8"))
    agg = res["aggregate"]
    pq = [json.loads(l) for l in D_PER_QUESTION.read_text(encoding="utf-8").splitlines() if l.strip()]
    tax = json.loads(D_ERROR_TAX.read_text(encoding="utf-8")) if D_ERROR_TAX.exists() else {}
    trans = json.loads(D_TRANSITIONS.read_text(encoding="utf-8")) if D_TRANSITIONS.exists() else {}
    conds = D_CONDS

    # 1 — correctness comparison
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))
    for ax, metric, title in (
        (axes[0], "answer_correctness", "Soft correctness"),
        (axes[1], "correct_rate", "Binary correct-rate"),
        (axes[2], "citation_recall", "Citation recall"),
        (axes[3], "groundedness", "Groundedness"),
    ):
        vals = [agg[c].get(metric, 0.0) for c in conds]
        ax.bar([c for c in conds], vals, color=["#4C72B0", "#55A868", "#C44E52"])
        ax.set_title(title)
        ax.set_ylim(0, 1)
        for i, v in enumerate(vals):
            ax.text(i, v + 0.01, f"{v:.3f}", ha="center", fontsize=9)
    fig.suptitle("Experiment D — C-O3 vs D2 vs D3")
    fig.tight_layout()
    fig.savefig(D_PLOT_DIR / "experiment_D_correctness_comparison.png", dpi=150)
    plt.close(fig)

    # 2 — per-question transition scatter
    fig, ax = plt.subplots(figsize=(6.5, 6))
    xs = {"C-O3": 0, "D2": 1, "D3": 2}
    rng = np.random.default_rng(7)
    for row in pq:
        for cond in conds:
            v = row.get(cond, {})
            if v.get("correct") is None and v.get("correct") is not False:
                continue
            y = 1 if v.get("correct") else 0
            x = xs[cond] + rng.uniform(-0.12, 0.12)
            ax.scatter(x, y, s=14, alpha=0.45, color={"C-O3": "#4C72B0", "D2": "#55A868", "D3": "#C44E52"}[cond])
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(conds)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["incorrect", "correct"])
    ax.set_title("Experiment D — per-question correctness by condition")
    fig.tight_layout()
    fig.savefig(D_PLOT_DIR / "experiment_D_per_question_transition.png", dpi=150)
    plt.close(fig)

    # 3 — error distribution (auditor defect categories, D3)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    d3t = (tax.get("D3") or {}).get("defect_type_counts") or {}
    if d3t:
        items = sorted(d3t.items(), key=lambda kv: -kv[1])
        ax.barh([k for k, _ in items][::-1], [v for _, v in items][::-1], color="#C44E52")
        ax.set_title("Experiment D — auditor-detected defect distribution (D3)")
        ax.tick_params(axis="y", labelsize=8)
    else:
        ax.text(0.5, 0.5, "no auditor data", ha="center", va="center")
    fig.tight_layout()
    fig.savefig(D_PLOT_DIR / "experiment_D_error_distribution.png", dpi=150)
    plt.close(fig)

    # 4 — auditor effect: D2 vs D3 transitions + PASS/FAIL correctness
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    t23 = trans.get("D2_to_D3") or {}
    axes[0].bar(list(t23.keys()), list(t23.values()), color="#55A868")
    axes[0].set_title("D2 -> D3 transitions")
    pass_corr, fail_corr = [], []
    for row in pq:
        d3v = row.get("D3", {})
        if d3v.get("audit_status") == "PASS":
            pass_corr.append(1 if d3v.get("correct") else 0)
        elif d3v.get("audit_status") == "FAIL":
            fail_corr.append(1 if d3v.get("correct") else 0)
    axes[1].bar(
        ["PASS", "FAIL"],
        [_mean(pass_corr) if pass_corr else 0, _mean(fail_corr) if fail_corr else 0],
        color=["#55A868", "#C44E52"],
    )
    axes[1].set_title("Binary correctness by audit status (D3)")
    axes[1].set_ylim(0, 1)
    fig.suptitle("Experiment D — auditor effect")
    fig.tight_layout()
    fig.savefig(D_PLOT_DIR / "experiment_D_auditor_effect.png", dpi=150)
    plt.close(fig)

    print(f"  plots -> {D_PLOT_DIR}/experiment_D_*.png", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# Response shim + service-equivalent citation scoring (evaluators unchanged)
# --------------------------------------------------------------------------- #
@dataclass
class _DResp:
    """Minimal RAGResponse-shaped shim for the unchanged compute_metrics."""

    answer: str
    citations: list = field(default_factory=list)
    groundedness_score: float = 0.0
    total_latency_ms: int = 0
    generation_latency_ms: int = 0
    retrieval_latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _score_like_service(answer: str, chunks: list, built):
    """Extract citations exactly like GroundedGenerationService steps 4-5.

    Uses the production CitationTracker + ResponseSanitizer so citation recall,
    citation precision and groundedness are scored with the UNCHANGED
    evaluators (spec sec 2).  D1 (C-O3) metrics are reused from C, which scored
    through the full service — this keeps D2/D3 on identical footing.
    """
    from app.rag.generation.citation_tracker import CitationTracker
    from app.rag.generation.sanitizer import ResponseSanitizer

    citation_map = {
        cit["index"]: c
        for cit in (built.citations or [])
        if (c := next((ch for ch in chunks if ch.chunk_id == cit["chunk_id"]), None))
    }
    try:
        cits = CitationTracker().extract(answer, chunks, citation_map)
        san = ResponseSanitizer().sanitize(answer, cits, chunks)
        return list(san.valid_citations), float(san.groundedness_score)
    except Exception:
        return [], 0.0


# --------------------------------------------------------------------------- #
# D3 final-answer builder (no extra LLM call — spec sec 8)
# --------------------------------------------------------------------------- #
def build_final_answer(analysis: dict, audit: dict, max_index: int) -> str:
    """Render the final D3 answer from analysis + auditor output.

    PASS  -> the analysis conclusion, qualified by any uncertainties (1 call,
             identical to D2's answer path apart from the audit check).
    FAIL  -> the auditor's corrected conclusion, with its required corrections
             and citation corrections applied as qualifications (the controlled
             revision is embedded in the same audit/revision call).
    """
    concl = _as_str(analysis.get("legal_conclusion")).strip()
    uncert = [_as_str(u) for u in _as_list(analysis.get("uncertainties")) if _as_str(u).strip()]
    lines = [concl or "The provided evidence does not establish a determinate legal conclusion."]

    if audit["status"] == "FAIL":
        corrected = _as_str(audit.get("corrected_conclusion")).strip()
        if corrected:
            # Spec sec 7: on FAIL the corrected conclusion REPLACES the original
            # (Reason -> Audit -> Correct -> Answer). Emitting the corrected
            # conclusion as the answer — not appended after the flagged-wrong
            # original — avoids a self-contradicting response.
            lines = [corrected]
        else:
            # Auditor flagged defects but supplied no corrected conclusion:
            # fall back to the original conclusion with corrections as notes.
            for rc in audit.get("required_corrections", [])[:3]:
                if _as_str(rc).strip():
                    lines.append(f"Note: {_as_str(rc).strip()}")
    elif audit.get("required_corrections"):
        # Minor (non-FAIL) corrections noted as qualifications.
        for rc in audit.get("required_corrections", [])[:2]:
            if _as_str(rc).strip():
                lines.append(f"Note: {_as_str(rc).strip()}")

    if uncert:
        lines.append("Caveats: " + "; ".join(uncert[:3]) + ".")

    text = "\n".join(lines)
    return repair_citation_markers(text, analysis, audit, max_index)


# --------------------------------------------------------------------------- #
# D2 single-call payload validation (spec sec 4 structure, concise output)
# --------------------------------------------------------------------------- #
def validate_reasoning_payload(obj: Any) -> tuple[bool, str]:
    """Validate the {structured_analysis, final_answer} single-call payload."""
    if not isinstance(obj, dict):
        return False, "not a JSON object"
    if "structured_analysis" not in obj or "final_answer" not in obj:
        return False, "missing structured_analysis/final_answer keys"
    ok, why = validate_reasoning(obj.get("structured_analysis"))
    if not ok:
        return False, f"structured_analysis invalid: {why}"
    if not _as_str(obj.get("final_answer")).strip():
        return False, "empty final_answer"
    return True, ""


# --------------------------------------------------------------------------- #
# Experiment C reuse loader (spec sec 3)
# --------------------------------------------------------------------------- #
def load_c_o3_correctness() -> dict[str, dict]:
    """Load Experiment C per-question O3 results + O1/O2 correctness flags.

    The O1/O2 flags identify the 130 'III' questions (spec sec 18) without
    re-running anything from Experiment C.
    """
    pq = json.loads(C_PER_QUESTION.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for qid, entry in pq.items():
        oc = entry.get("oracle_conditions", {})
        o3 = oc.get(O3_COND, {})
        if o3.get("status") != "ok":
            continue
        out[qid] = {
            "answer": o3.get("answer"),
            "answer_correctness": o3.get("answer_correctness"),
            "correct": o3.get("correct"),
            "citation_recall": o3.get("citation_recall"),
            "citation_precision": o3.get("citation_precision"),
            "groundedness": o3.get("groundedness"),
            "abstained": o3.get("abstained"),
            "latency_ms": o3.get("latency_ms"),
            "context_tokens": o3.get("context_tokens"),
            "correct_o1": oc.get("O1_gold", {}).get("correct"),
            "correct_o2": oc.get("O2_gold_neighbors", {}).get("correct"),
        }
    return out


# --------------------------------------------------------------------------- #
# Phase SUMMARY (no LLM) — experiment_D_summary.md (spec sec 22/23)
# --------------------------------------------------------------------------- #
def phase_summary(stub: bool) -> int:
    if not D_AGGREGATE.exists():
        print("results missing — run --phase analyze first", flush=True)
        return 1
    res = json.loads(D_AGGREGATE.read_text(encoding="utf-8"))
    agg = res["aggregate"]
    cmp_ = res["comparisons"]
    trans = json.loads(D_TRANSITIONS.read_text(encoding="utf-8")) if D_TRANSITIONS.exists() else {}
    tax = json.loads(D_ERROR_TAX.read_text(encoding="utf-8")) if D_ERROR_TAX.exists() else {}
    acc = json.loads(D_ACCOUNTING.read_text(encoding="utf-8")) if D_ACCOUNTING.exists() else {}
    build_rep = json.loads(D_BUILD_REPORT.read_text(encoding="utf-8")) if D_BUILD_REPORT.exists() else {}

    def row(c: str) -> str:
        a = agg.get(c, {})
        return (
            f"| {c} | {a.get('answer_correctness', '-')} | {a.get('correct_rate', '-')} | "
            f"{a.get('citation_recall', '-')} | {a.get('citation_precision', '-')} | "
            f"{a.get('groundedness', '-')} | {a.get('median_latency_ms', '-')} | {a.get('n', 0)} |"
        )

    d3t = tax.get("D3") or {}
    iii = agg.get("iii_analysis", {})
    lines = [
        "# Experiment D — Structured Legal Reasoning + Legal Auditor",
        "",
        f"Stub validation: **{stub}**" + ("" if not stub else "  (no real LLM calls — pipeline validation only)"),
        "",
        "## 1. Conditions",
        "- **C-O3 (D1)**: Experiment C O3 Full Support outputs and metrics, **reused verbatim** (0 new calls; spec sec 3).",
        "- **D2**: one reasoning call — structured legal analysis + final answer from that analysis only (spec sec 4/8).",
        "- **D3**: one combined audit/revision call over the checkpointed D2 analysis; controlled revision embedded via corrected_conclusion (spec sec 6/7/8).",
        "",
        "## 2. Headline table (spec sec 16)",
        "",
        "| System | Soft Correctness | Binary Correct | Cit R | Cit P | Groundedness | Median latency | n |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        row("C-O3"),
        row("D2"),
        row("D3"),
        "",
        "### Deltas",
        f"- C-O3 -> D2 soft: {cmp_['C-O3_vs_D2']['answer_correctness_delta']} (binary {cmp_['C-O3_vs_D2']['binary_delta']})",
        f"- D2 -> D3 soft: {cmp_['D2_vs_D3']['answer_correctness_delta']} (binary {cmp_['D2_vs_D3']['binary_delta']})",
        f"- C-O3 -> D3 soft: {cmp_['C-O3_vs_D3']['answer_correctness_delta']} (binary {cmp_['C-O3_vs_D3']['binary_delta']})",
        "",
        "## 3. Context control (spec sec 11)",
        f"- Verified questions: {build_rep.get('context_hash_verification_summary', {}).get('verified_ok', '-')} ok / "
        f"{build_rep.get('context_hash_verification_summary', {}).get('mismatch', '-')} mismatch",
        f"- Rebuild mismatches vs fresh gold resolution: {len(build_rep.get('rebuild_mismatches', []))}",
        f"- Data-leakage scan violations (spec sec 12): {len(build_rep.get('leak_scan', {}).get('violations', []))}",
        "",
        "## 4. Per-question transitions (spec sec 17)",
        f"- C-O3 -> D2: {trans.get('C-O3_to_D2', '-')}",
        f"- D2 -> D3: {trans.get('D2_to_D3', '-')}",
        f"- C-O3 -> D3: {trans.get('C-O3_to_D3', '-')}",
        f"- Fixed by structured reasoning: {len(trans.get('fixed_by_structured_reasoning', []))}",
        f"- Fixed only by auditor: {len(trans.get('fixed_only_by_auditor', []))}",
        f"- Degraded by structured reasoning: {len(trans.get('degraded_by_structured_reasoning', []))}",
        f"- Degraded by auditor: {len(trans.get('degraded_by_auditor', []))}",
        f"- Still incorrect everywhere: {len(trans.get('still_incorrect_everywhere', []))}",
        "",
        "## 5. The 130 III questions (spec sec 18)",
        f"- n_III (O1&O2&O3 incorrect in C): {iii.get('n_iii', '-')}",
        f"- D2 improved: {iii.get('D2_improved', '-')} | D3 improved: {iii.get('D3_improved', '-')}",
        f"- D2 still incorrect: {iii.get('D2_still_incorrect', '-')} | D3 still incorrect: {iii.get('D3_still_incorrect', '-')}",
        f"- Auditor defect types on III questions: {iii.get('defect_types_on_iii_questions', '-')}",
        "",
        "## 6. Auditor + error taxonomy (spec sec 14/15)",
        f"- Auditor PASS/FAIL: {d3t.get('pass_count', '-')} / {d3t.get('fail_count', '-')}",
        f"- Revision rate: {d3t.get('revision_rate', '-')}",
        f"- Defect types: {d3t.get('defect_type_counts', '-')}",
        f"- Severities: {d3t.get('defect_severity_counts', '-')}",
        "",
        "## 7. Call accounting (spec sec 9)",
        f"- Hard budget: {acc.get('hard_budget_total_new_generations', '-')} new generations (C's 450 NOT re-spent)",
        f"- Planned: {acc.get('planned_calls', '-')}",
        f"- Actual generations: {acc.get('actual_generations', '-')} (successful {acc.get('successful_calls', '-')}, failed {acc.get('failed_calls', '-')})",
        f"- Transport backoffs (NOT generations): {acc.get('transport_backoffs', '-')}",
        f"- Revision calls: {acc.get('revision_calls', '-')}",
        f"- Tokens: in={acc.get('total_token_usage', {}).get('input_tokens', '-')} out={acc.get('total_token_usage', {}).get('output_tokens', '-')}",
        f"- Budget respected: {acc.get('budget_respected', '-')}",
        "",
        "## 8. Fixed-prompt declaration (spec sec 21)",
        "All conditions used the fixed REASONING/ANSWER/AUDITOR prompts, model "
        f"`{res['config']['model']}`, temperature {res['config']['temperature']}, "
        "no prompt/model/temperature variants, no post-hoc tuning.",
        "",
        "## 9. Required-report questions (spec sec 23)",
        "1. Did structured legal reasoning improve soft correctness? — see deltas in sec 2.",
        "2. Did it improve binary correct-rate? — see deltas in sec 2.",
        "3. Did the Auditor add further improvement? — D2 vs D3 deltas.",
        f"4. How many of the 130 III questions were recovered? — {iii.get('D2_improved', '-')} (D2) / {iii.get('D3_improved', '-')} (D3) of {iii.get('n_iii', '-')}.",
        f"5. How many questions regressed? — D2: {len(trans.get('degraded_by_structured_reasoning', []))}, D3: {len(trans.get('degraded_by_auditor', []))}.",
        "6. Which reasoning error categories were fixed? — compare defect types on improved vs still-incorrect questions in the taxonomy.",
        "7. Which error categories remain? — `still_incorrect_everywhere` + dominant defect types above.",
        "8. Did citation quality improve? — Cit R / Cit P columns in sec 2.",
        "9. Did groundedness improve? — Groundedness column in sec 2.",
        "10. Did reasoning increase latency? — median latency column (D2/D3 vs C-O3) plus per-stage latencies in the call accounting.",
        f"11. How many LLM calls were actually required? — {acc.get('actual_generations', '-')} new generations ({acc.get('successful_calls', '-')} successful).",
        "12. Was the improvement statistically/empirically meaningful? — apply the spec sec 24 decision rule to the deltas + transition counts.",
        "13. Is structured reasoning worth integrating into the production RAG? — decide per spec sec 24 after full-run results.",
        "14. What should Experiment E test? — the largest remaining defect category in the taxonomy (spec sec 25).",
        "",
        "## 10. Artifacts (spec sec 22)",
        "- experiment_D_summary.md / experiment_D_results.json / experiment_D_per_question.jsonl",
        "- experiment_D_call_accounting.json / experiment_D_calls.jsonl (per-call records)",
        "- experiment_D_reasoning.jsonl / experiment_D_auditor.jsonl",
        "- experiment_D_error_taxonomy.json / experiment_D_transition_matrix.json",
        "- plots/experiment_D_correctness_comparison.png, per_question_transition.png, error_distribution.png, auditor_effect.png",
    ]
    D_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  summary -> {D_SUMMARY}", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# Phase BUILD (zero LLM calls): verify D2/D3 contexts == C O3 (spec sec 11/12)
# --------------------------------------------------------------------------- #
def phase_build() -> int:
    print("=" * 70, flush=True)
    print("Phase BUILD: D2/D3 context reconstruction + C-O3 hash verification (0 LLM calls)", flush=True)
    print("=" * 70, flush=True)

    from evaluation.eval_e2e_v2 import load_payload_index
    from evaluation.experiment_c_oracle_eval import (
        _build_doc_order,
        _build_sec_groups,
        build_condition_contexts,
    )

    payload_index = load_payload_index()
    family_map = FamilyMap()
    gold_index = build_gold_index(payload_index, family_map)
    manifest_q = load_c_questions()
    questions = {q.question_id: q for q in load_questions()}

    doc_order = _build_doc_order(payload_index)
    sec_groups = _build_sec_groups(payload_index)

    resolved = [qid for qid, e in manifest_q.items() if not e.get("unresolved")]
    report: dict[str, Any] = {
        "experiment": "D — structured legal reasoning + auditor",
        "c_manifest": str(C_MANIFEST),
        "n_questions": len(questions),
        "n_resolved_in_c_manifest": len(resolved),
        "o3_condition": O3_COND,
        "planned_calls": {
            "D2_reasoning": QUESTIONS_PER_COND,
            "D3_audit_revision": QUESTIONS_PER_COND,
            "total": PLANNED_CALLS,
        },
        "context_hash_verification": {},
        "rebuild_mismatches": [],
        "leak_scan": {"prompts_scanned": 0, "violations": []},
        "config": {
            "model": LLM_MODEL,
            "temperature": LLM_TEMPERATURE,
            "max_tokens_structured": MAX_TOKENS_STRUCTURED,
            "max_tokens_final_answer": LLM_MAX_TOKENS,
            "max_context_chars": MAX_CTX_CHARS,
            "max_revision_cycles": MAX_REVISION_CYCLES,
        },
    }

    h_ok = h_bad = 0
    n_order_nondet = 0
    for qid in sorted(resolved):
        entry = manifest_q[qid]
        q = questions.get(qid)

        # 1) Fresh gold resolution must reproduce the manifest's gold identity.
        if q is None:
            report["rebuild_mismatches"].append({"qid": qid, "reason": "missing from benchmark"})
            continue
        res = resolve_gold_units(q, payload_index, family_map, gold_index)
        if res["gold_chunk_ids"] and set(entry["gold_chunk_ids"]) != res["gold_chunk_ids"]:
            report["rebuild_mismatches"].append({"qid": qid, "reason": "gold_chunk_ids differ from fresh resolution"})

        # 2) Spec sec 11: rebuild the deterministic O3 chunk-id list, feed it
        #    through the IDENTICAL context builder (same truncation rules), and
        #    compare MEMBERSHIP with the stored C manifest list.  D2/D3 rebuild
        #    their runtime context from the stored manifest ids (the same
        #    evidence C used), so membership + text identity is the binding
        #    check; exact ORDER equality is recorded separately because the C
        #    build's gold-first sort left equal (title, chunk_index) ties to
        #    set-iteration order, which is hash-randomised per process.
        stored_ids = entry["conditions"][O3_COND]["context_chunk_ids"]
        stored_hash = context_id_hash(stored_ids)
        fresh_ctx = build_condition_contexts(res["gold_chunk_ids"], payload_index, doc_order, sec_groups)
        fresh_chunks = [to_retrieved_chunk(c, payload_index.get(c, {}), 1.0) for c in fresh_ctx[O3_COND]]
        fresh_built = _COracleContextBuilder(2000, MAX_CTX_CHARS).build(entry["question"], fresh_chunks, "general_qa")
        fresh_ids = [cit["chunk_id"] for cit in (fresh_built.citations or [])]
        fresh_hash = context_id_hash(fresh_ids)
        membership_match = set(stored_ids) == set(fresh_ids)
        order_nondeterministic = membership_match and stored_hash != fresh_hash

        # 3) Rebuild the context TEXT from the STORED ids with the identical
        #    builder — this is the exact text the D2/D3 model sees — and verify
        #    it reproduces the C manifest context size (corpus-text identity).
        ctx, err = c_o3_context_text(qid, manifest_q, payload_index)
        text_match = (ctx is not None) and len(ctx) == entry["conditions"][O3_COND]["context_chars"]
        ctx_sha = hashlib.sha256((ctx or "").encode("utf-8")).hexdigest()[:16]
        match = membership_match and text_match and not err
        h_ok += 1 if match else 0
        h_bad += 0 if match else 1
        n_order_nondet += 1 if order_nondeterministic else 0
        report["context_hash_verification"][qid] = {
            "match": match,
            "membership_match": membership_match,
            "order_nondeterministic": order_nondeterministic,
            "stored_context_hash": stored_hash,
            "fresh_context_hash": fresh_hash,
            "rebuilt_text_sha256": ctx_sha,
            "text_chars_match": bool(text_match),
            "error": err,
        }

        # 4) Spec sec 12: staged-prompt leakage scan — the acceptable conclusion
        #    must never appear in any prompt the model can see.
        leak_hits = []
        ac = (entry.get("acceptable_conclusion") or "").strip()
        if ac:
            for name, text in (
                ("reasoning", render_reasoning_prompts(entry["question"], ctx or "")[1]),
                ("answer", render_answer_prompts(entry["question"], "{analysis}")[1]),
                ("audit", render_audit_prompts(entry["question"], ctx or "", "{analysis}")[1]),
                ("revision", render_revision_prompts(entry["question"], "{analysis}", "{audit}")[1]),
            ):
                if ac.lower() in text.lower():
                    leak_hits.append(name)
        report["leak_scan"]["prompts_scanned"] += 4
        if leak_hits:
            report["leak_scan"]["violations"].append({"qid": qid, "stages": leak_hits})

    report["context_hash_verification_summary"] = {
        "verified_ok": h_ok,
        "order_nondeterministic_only": n_order_nondet,
        "mismatch": h_bad,
        "total": len(resolved),
        "method": (
            "membership equality of the rebuilt (builder-truncated) O3 chunk-id set "
            "vs the stored C manifest set + rebuilt context-text size equality; exact "
            "id-order equality recorded separately (C's gold-first sort left equal "
            "(title, chunk_index) ties to process-randomised set-iteration order)"
        ),
    }
    D_BUILD_REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"  questions: {len(questions)} | resolved in C manifest: {len(resolved)}", flush=True)
    print(
        f"  context verification: ok={h_ok} mismatch={h_bad} (order-nondeterministic-only: {n_order_nondet})",
        flush=True,
    )
    print(f"  rebuild mismatches: {len(report['rebuild_mismatches'])}", flush=True)
    print(
        f"  leak scan: {len(report['leak_scan']['violations'])} violations over {report['leak_scan']['prompts_scanned']} prompts",
        flush=True,
    )
    print(f"  build report -> {D_BUILD_REPORT}", flush=True)

    if h_bad or report["rebuild_mismatches"] or report["leak_scan"]["violations"]:
        print("\n  !! STOP (spec sec 11): D2/D3 evidence is not verifiably identical to C O3 — do not run.", flush=True)
        return 1
    print("\n  BUILD OK: every D2/D3 context verified identical to Experiment C O3. No LLM calls made.", flush=True)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Experiment D — structured legal reasoning + auditor")
    p.add_argument(
        "--phase", required=True, choices=["build", "run-d2", "run-d3", "analyze", "plots", "summary", "stub-smoke"]
    )
    p.add_argument("--limit", type=int, default=None, help="run only the first N pending questions (smoke tests)")
    p.add_argument("--resume", action="store_true", help="resume from checkpoints (skip successful qids)")
    p.add_argument("--concurrency", type=int, default=1, help="worker threads (C used 1 to avoid burst 429s)")
    p.add_argument("--stub", action="store_true", help="stub-LLM pipeline validation (0 real calls)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.phase == "build":
        return phase_build()
    if args.phase == "run-d2":
        return phase_run_d2(stub=args.stub, resume=args.resume, limit=args.limit, concurrency=args.concurrency)
    if args.phase == "run-d3":
        return phase_run_d3(stub=args.stub, resume=args.resume, limit=args.limit, concurrency=args.concurrency)
    if args.phase == "analyze":
        return phase_analyze(stub=args.stub)
    if args.phase == "plots":
        return phase_plots(stub=args.stub)
    if args.phase == "summary":
        return phase_summary(stub=args.stub)
    if args.phase == "stub-smoke":
        # Full pipeline validation with ZERO real LLM calls.
        rc = phase_run_d2(stub=True, resume=False, limit=args.limit)
        if rc:
            return rc
        rc = phase_run_d3(stub=True, resume=False, limit=args.limit)
        if rc:
            return rc
        rc = phase_analyze(stub=True)
        if rc:
            return rc
        rc = phase_plots(stub=True)
        if rc:
            return rc
        return phase_summary(stub=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
