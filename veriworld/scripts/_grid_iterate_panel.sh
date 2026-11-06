#!/usr/bin/env bash
# Multi-model panel for harness_grid_iterate. 6 tasks × 12 models
# (split into 2 chunks of 6 due to max_instances=6). 12 orchestrator
# invocations total.
#
# Usage:  bash veriworld/scripts/_grid_iterate_panel.sh 2>&1 \
#           | tee runs/grid_iterate_panel_$(date +%Y%m%d_%H%M%S).log

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

TASKS=(
  find_wooden_spoon_pot_view
  find_black_spatula_pot_view
  find_pot_cooktop_view
  find_pan_cooktop_view
  find_blue_mug_default_view
  find_knife_cutting_view
)

invoke() {
  local task="$1" chunk="$2" models
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  echo
  echo "============================================================"
  echo "[$(date '+%H:%M:%S')] grid_iterate | $task | chunk=$chunk"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate \
    --task-id "$task" \
    --models "$models" \
    --seeds 0 \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 6 \
    --log-level INFO || echo "  ⚠ chunk failed: $task/$chunk — continuing"
  sleep 5
}

for task in "${TASKS[@]}"; do
  invoke "$task" A
  invoke "$task" B
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] grid panel complete."
