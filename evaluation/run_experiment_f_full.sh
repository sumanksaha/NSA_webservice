#!/usr/bin/env bash
# Experiment F orchestrator (design v3): run F1+F2 (resumable, budget-capped), then analyze.
# Usage: nohup bash evaluation/run_experiment_f_full.sh [--stub] > evaluation/out/ceiling_v5/_run_orchestrator_F.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
STUB_FLAG="${1:-}"

echo "[orchestrator-F] start $(date)"
python evaluation/experiment_f_calibration_eval.py $STUB_FLAG --layer both --concurrency 3
echo "[orchestrator-F] run phase done $(date)"

python evaluation/experiment_f_calibration_eval.py $STUB_FLAG --analyze
echo "[orchestrator-F] analyze done $(date)"
echo "[orchestrator-F] complete"
