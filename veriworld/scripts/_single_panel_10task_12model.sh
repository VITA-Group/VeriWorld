#!/usr/bin/env bash
# 10 kitchen tasks × 12 models × N seeds, single-ball harness (no
# grid scaffold). max-rounds=8.

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

if [ "$#" -ge 1 ]; then SEEDS=("$@"); else SEEDS=(0 1 2); fi

invoke() {
  local task="$1" chunk="$2" seed="$3" models
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] seed=$seed task=$task chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate \
    --task-id "$task" \
    --models "$models" \
    --seeds "$seed" \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 8 \
    --random-view \
    --log-level WARNING || echo "  ⚠ chunk failed: seed=$seed $task/$chunk — continuing"
  sleep 5
}

for seed in "${SEEDS[@]}"; do
  echo
  echo "############################################################"
  echo "# SEED $seed — fresh random views for all ${#TASKS[@]} tasks"
  echo "############################################################"
  for task in "${TASKS[@]}"; do
    invoke "$task" A "$seed"
    invoke "$task" B "$seed"
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all single-iterate seeds × tasks × 12 models done."
