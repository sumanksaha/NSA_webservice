#!/usr/bin/env bash
# Experiment D — full-run orchestrator.
# Chains: D2 (150 reasoning calls) -> D3 (150 audit calls) -> analyze -> plots -> summary.
# All phases are checkpoint-resumable; re-running this script after an interruption
# resumes from the last completed question.
set -u
cd "$(dirname "$0")/.."
OUT=evaluation/out/ceiling_v5

echo "[orchestrator] start $(date)"
python evaluation/experiment_d_reasoning_eval.py --phase run-d2 --resume --concurrency 3 > "$OUT/_run_D2.log" 2>&1
echo "[orchestrator] run-d2 exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase run-d3 --resume --concurrency 3 > "$OUT/_run_D3.log" 2>&1
echo "[orchestrator] run-d3 exit=$? $(date)"

# Fixed FAIL-path renderer: deterministic re-render of all D3 answers from
# stored analysis+audit artifacts (0 LLM calls). Required before analyze.
python evaluation/rerender_d3_answers.py > "$OUT/_run_D_rerender.log" 2>&1
echo "[orchestrator] re-render exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase analyze > "$OUT/_run_D_analyze.log" 2>&1
echo "[orchestrator] analyze exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase plots >> "$OUT/_run_D_analyze.log" 2>&1
echo "[orchestrator] plots exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase summary > "$OUT/_run_D_summary.log" 2>&1
echo "[orchestrator] summary exit=$? $(date)"

echo "[orchestrator] done $(date)"
