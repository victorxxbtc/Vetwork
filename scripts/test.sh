#!/usr/bin/env bash
# One command for every test: Python suite, contract unit tests, wasm build, sandbox tests.
# Sandbox: near-workspaces downloads neard on first run, or set NEAR_SANDBOX_BIN_PATH.
# Set VETWORK_SKIP_SANDBOX=1 to skip the sandbox step (e.g. offline without a neard binary).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest -q
cd contract
cargo test --lib
cargo near build non-reproducible-wasm
cargo test --test sandbox
cargo test --test attacks -- --nocapture | grep -E "refused|ACCEPTED"
cp target/attacks.json ../docs/attacks.json
cargo test --test reputation -- --nocapture | grep -E "events;"
cp target/events.jsonl ../docs/sandbox-events.jsonl
cp target/reputation.json ../docs/sandbox-reputation.json
