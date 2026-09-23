#!/usr/bin/env bash
# Finalization watcher for Experiment D.
# Waits for the (already-running) D3 worker to exit, then:
#   1. re-renders all D3 answers with the fixed FAIL-path renderer (0 LLM calls)
#   2. runs analyze -> plots -> summary
set -u
cd "$(dirname "$0")/.."
OUT=evaluation/out/ceiling_v5
D3PID="$1"

echo "[finalize] watching D3 worker PID $D3PID"
while ps -p "$D3PID" > /dev/null 2>&1; do sleep 30; done
echo "[finalize] D3 worker finished $(date)"

# One resume pass: retries any D3 questions that errored in the main pass
# (e.g. output-cap truncation). Error records are always retried; successes
# are never re-run (checkpoint semantics). Costs at most a few generations.
python evaluation/experiment_d_reasoning_eval.py --phase run-d3 --resume --concurrency 3 >> "$OUT/_run_D3.log" 2>&1
echo "[finalize] d3-resume exit=$? $(date)"

# Guard: require a real D3 python process still running (avoid acting on a wrong PID).
echo "[finalize] re-rendering D3 answers with fixed FAIL-path renderer (0 LLM calls)"
python evaluation/rerender_d3_answers.py > "$OUT/_run_D_rerender.log" 2>&1
echo "[finalize] re-render exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase analyze > "$OUT/_run_D_analyze.log" 2>&1
echo "[finalize] analyze exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase plots >> "$OUT/_run_D_analyze.log" 2>&1
echo "[finalize] plots exit=$? $(date)"

python evaluation/experiment_d_reasoning_eval.py --phase summary > "$OUT/_run_D_summary.log" 2>&1
echo "[finalize] summary exit=$? $(date)"

echo "[finalize] done $(date)"
