#!/usr/bin/env bash
# Local test runner — mirrors the two CI shards in .github/workflows/validation.yml.
#
# Usage:
#   scripts/test.sh              # fast shard (same as CI "fast-tests": -m "not slow")
#   scripts/test.sh --slow       # slow shard with coverage (same as CI "test-slow")
#   scripts/test.sh --all        # everything, no marker filter
#   scripts/test.sh tests/test_rbac.py -x -q   # any args pass straight to pytest
#
# Uses the repo venv (venv/bin/python) so local runs match CI's dependency set.
# Falls back to `python3 -m pytest` when the venv has no pytest installed.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x "venv/bin/python" ] && venv/bin/python -m pytest --version >/dev/null 2>&1; then
  PY=venv/bin/python
elif python3 -m pytest --version >/dev/null 2>&1; then
  PY=python3
else
  echo "error: pytest not found. Install dev deps: pip install -r requirements-dev.txt" >&2
  exit 1
fi

case "${1:---fast}" in
  --fast|-f)
    shift 2>/dev/null || true
    exec "$PY" -m pytest -m "not slow" --tb=short -q "$@"
    ;;
  --slow|-s)
    shift 2>/dev/null || true
    exec "$PY" -m pytest -m "slow" --cov=app --cov-report=term-missing --tb=short -q "$@"
    ;;
  --all|-a)
    shift 2>/dev/null || true
    exec "$PY" -m pytest --tb=short -q "$@"
    ;;
  *)
    exec "$PY" -m pytest --tb=short -q "$@"
    ;;
esac
