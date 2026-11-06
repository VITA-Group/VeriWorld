#!/usr/bin/env bash
# 10 kitchen tasks × 12 models × N seeds, GRID-1 (single-ball) OFF-CENTRE
# harness. Direct ablation of _gridoff_panel_10task_12model.sh:
# identical view_seed formula + identical aim_offset_range_cm=(20, 50)
# means the camera poses are byte-identical for matching (seed, task);
# the ONLY difference is round-1 spawns 1 ball at the optical-axis
# raycast hit instead of a 3×3 grid.
#
# 4-instance variant: 3 chunks of 4 models each (instead of 2 chunks
# of 6) so we only consume 4 UE slots concurrently — leaves headroom
# on the GPU for testing other harnesses in parallel.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro"
CHUNK_B="gpt-4o,gpt-5-mini,gpt-5.2,qwen3-vl-30b"
CHUNK_C="qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
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
  case "$chunk" in
    A) models="$CHUNK_A" ;;
    B) models="$CHUNK_B" ;;
    C) models="$CHUNK_C" ;;
  esac
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] seed=$seed task=$task chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate_offcenter \
    --task-id "$task" \
    --models "$models" \
    --seeds "$seed" \
    --max-instances 4 \
    --build "$BUILD" \
    --max-rounds 10 \
    --random-view \
    --log-level WARNING || echo "  ⚠ chunk failed: seed=$seed $task/$chunk — continuing"
  sleep 5
}

for seed in "${SEEDS[@]}"; do
  echo
  echo "############################################################"
  echo "# SEED $seed — grid-1 off-centre (4-instance, 3 chunks), ${#TASKS[@]} tasks"
  echo "############################################################"
  for task in "${TASKS[@]}"; do
    invoke "$task" A "$seed"
    invoke "$task" B "$seed"
    invoke "$task" C "$seed"
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all single_iterate_offcenter seeds done."
