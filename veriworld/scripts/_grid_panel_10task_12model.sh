#!/usr/bin/env bash
# 10 tasks × 12 models, each task uses one fixed random-view (so all
# 12 models see the SAME camera pose for fair comparison). Single-ball,
# 6 rounds per episode. Uses run_parallel which auto-batches into
# 6-model chunks under max-instances=6.
#
# Total: 10 task invocations × 2 chunks (12 models split 6+6) =
# 20 sub-invocations. Estimate ~5-15 min/chunk → 2-5 h total.
#
# Usage:
#   bash veriworld/scripts/_grid_panel_10task_12model.sh

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

TASKS=(
  find_pan_cooktop_view
  find_pot_cooktop_view
  find_wooden_spoon_pot_view
  find_wooden_spatula_pot_view
  find_black_spoon_pot_view
  find_black_spatula_pot_view
  find_knife_cutting_view
  find_blue_mug_default_view
  find_white_grey_mug_default_view
  find_coffee_mug_default_view
)

invoke() {
  local task="$1" chunk="$2" models
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] task=$task chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate \
    --task-id "$task" \
    --models "$models" \
    --seeds 0 \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 6 \
    --max-balls 1 \
    --grid-n 3 \
    --random-view \
    --log-level WARNING || echo "  ⚠ chunk failed: $task/$chunk — continuing"
  sleep 5
}

# Outer = task, inner = chunk. Per task we run all 12 models (chunk A
# then chunk B) before moving to the next task. The random view for
# each task is derived deterministically from the task_id (via
# zlib.crc32 in task.py), so different tasks get different views
# automatically without any extra parameter here.
for task in "${TASKS[@]}"; do
  invoke "$task" A
  invoke "$task" B
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all 10 tasks × 12 models done."
