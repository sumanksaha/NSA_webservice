#!/usr/bin/env bash
# Experiment E — full-run orchestrator (design: Experiment_E_Citation_Verification_Design.md).
# Chains: run E (E2 ablation is embedded, 0 calls; E1 repair <= 150-call cap)
#         -> analyze -> plots -> summary.
# All phases are checkpoint-resumable; re-running after an interruption resumes
# from the last completed question.  The D2 checkpoint is REUSED — no reasoning
# calls are made by this experiment.
set -u
cd "$(dirname "$0")/.."
OUT=evaluation/out/ceiling_v5

echo "[orchestrator-E] start $(date)"
python evaluation/experiment_e_citation_verification.py --phase run --resume --concurrency 3 > "$OUT/_run_E.log" 2>&1
echo "[orchestrator-E] run exit=$? $(date)"

python evaluation/experiment_e_citation_verification.py --phase analyze >> "$OUT/_run_E.log" 2>&1
echo "[orchestrator-E] analyze exit=$? $(date)"

python evaluation/experiment_e_citation_verification.py --phase plots >> "$OUT/_run_E.log" 2>&1
echo "[orchestrator-E] plots exit=$? $(date)"

python evaluation/experiment_e_citation_verification.py --phase summary >> "$OUT/_run_E.log" 2>&1
echo "[orchestrator-E] summary exit=$? $(date)"

echo "[orchestrator-E] done $(date)"
