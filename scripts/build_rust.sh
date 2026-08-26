#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Render: best-effort native build of the nsa_rust PyO3 extension (rust/).
#
# Invoked from render.yaml buildCommand inside `( ... || true )`, so any
# failure here is NON-FATAL: on failure the app deploys on the pure-Python
# fallbacks wired through app.utils.rust_fuzzy, and the rust fast-path simply
# stays dormant. On success `import nsa_rust` resolves (RUST_AVAILABLE=True)
# and the native acceleration engages for fuzzy search + SparseRetriever.
#
# Render build env: Debian (Linux), root, bash. The C toolchain (gcc/ld) and
# Python.h come from packages in buildCommand (`build-essential` + Render's
# Python image dev headers). maturin targets the active venv interpreter.
# ---------------------------------------------------------------------------
set -u

# 1. Rust toolchain (stable). Skip if a prior build cached ~/.cargo.
if [ ! -f "$HOME/.cargo/env" ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

# 2. maturin (reads [tool.maturin] from pyproject.toml -> rust/Cargo.toml).
python -m pip install --quiet maturin

# 3. abi3 cdylib wheel -> target/wheels/, then install into the venv.
python -m maturin build --release
python -m pip install --no-deps --quiet target/wheels/nsa_rust-*.whl
