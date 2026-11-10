#!/usr/bin/env bash
# One command for every test: Python suite, contract unit tests, wasm build, sandbox tests.
# Sandbox: near-workspaces downloads neard on first run, or set NEAR_SANDBOX_BIN_PATH.
# Set ASSAY_SKIP_SANDBOX=1 to skip the sandbox step (e.g. offline without a neard binary).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest -q
cd contract
cargo test --lib
cargo near build non-reproducible-wasm
cargo test --test sandbox
