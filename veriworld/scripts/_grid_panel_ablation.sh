#!/usr/bin/env bash
# Grid-density ablation: rerun the 10-kitchen-task panel at grid_n=1
# and grid_n=4 (current baseline is grid_n=3). Same fixed seed=0 +
# random-view as the original panel run for apples-to-apples
# comparison.

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
  local task="$1" chunk="$2" gn="$3" models
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] grid_n=$gn task=$task chunk=$chunk models=$models"
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
    --grid-n "$gn" \
    --random-view \
    --log-level WARNING || echo "  ⚠ chunk failed: grid_n=$gn $task/$chunk — continuing"
  sleep 5
}

for gn in 1 4; do
  echo
  echo "############################################################"
  echo "# GRID_N $gn — full ${#TASKS[@]} tasks × 12 models"
  echo "############################################################"
  for task in "${TASKS[@]}"; do
    invoke "$task" A "$gn"
    invoke "$task" B "$gn"
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] grid-density ablation done."
