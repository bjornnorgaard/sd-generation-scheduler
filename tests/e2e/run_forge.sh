#!/usr/bin/env bash
# Start a throwaway Forge Neo with this extension and a fake generator, for browser testing.
#
# Nothing here touches a real Forge install's settings, models, extensions or GPU:
#   * --data-dir points at a scratch folder holding only this extension (symlinked) and the
#     fake generator, so no other extension is loaded;
#   * --ui-debug-mode skips loading a checkpoint and --cpu keeps CUDA untouched;
#   * it listens on its own port, so a running WebUI on 7860 is left alone.
#
#   FORGE_DIR   Forge Neo checkout (default: the Stability Matrix install)
#   E2E_DIR     scratch data dir (default: a fresh mktemp dir, printed on start)
#   E2E_PORT    port (default 7861)
#   E2E_STEP_SECONDS  fake seconds per sampling step, 8 steps per job (default 0.25)
#
# Stop it with:  pkill -f "data-dir $E2E_DIR"
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_ROOT="$(cd "$HERE/../.." && pwd)"
FORGE_DIR="${FORGE_DIR:-/opt/stabilitymatrix/Data/Packages/Stable Diffusion WebUI Forge - Neo}"
E2E_DIR="${E2E_DIR:-$(mktemp -d)}"
E2E_PORT="${E2E_PORT:-7861}"
export E2E_DIR E2E_STEP_SECONDS="${E2E_STEP_SECONDS:-0.25}"

mkdir -p "$E2E_DIR/extensions" "$E2E_DIR/outputs"
ln -sfn "$EXT_ROOT" "$E2E_DIR/extensions/sd-generation-scheduler"
ln -sfn "$HERE/fake_generation" "$E2E_DIR/extensions/e2e-fake-generation"
[ -f "$E2E_DIR/config.json" ] || echo '{"auto_launch_browser": "Disable"}' > "$E2E_DIR/config.json"

echo "E2E_DIR=$E2E_DIR  ->  http://127.0.0.1:$E2E_PORT" >&2
cd "$FORGE_DIR"
# The old-config notice waits for Enter; feed it one.
printf '\n' | exec "$FORGE_DIR/venv/bin/python" -u launch.py \
    --data-dir "$E2E_DIR" --gradio-allowed-path "$FORGE_DIR" \
    --ui-debug-mode --cpu --skip-version-check --skip-prepare-environment \
    --skip-torch-cuda-test --skip-python-version-check --skip-install \
    --port "$E2E_PORT"
