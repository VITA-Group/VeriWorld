#!/usr/bin/env bash
# Resume the bbox-v2 dual-harness sweep. The first run got stopped at
# 07:48 today, mid seed-0 / find_white_grey_mug_default_view chunk A.
#
# Already-completed cleanly (do NOT re-run):
#   harness_grid_iterate_offcenter_bboxv2, seed 0, tasks 1-8 (16 chunks)
#   = pan, pot, wooden_spoon, wooden_spatula, black_spoon, black_spatula,
#     knife, blue_mug
#
# Remaining (THIS script picks up here):
#   grid-3-bboxv2, seed 0, tasks 9-10 (white_grey_mug + coffee_mug, 4 chunks)
#   grid-3-bboxv2, seed 1, all 10 tasks                          (20 chunks)
#   grid-3-bboxv2, seed 2, all 10 tasks                          (20 chunks)
#   grid-1-bboxv2, seeds 0..2, all 10 tasks                      (60 chunks)
#
# = 104 panel-units. ~5-7 min each → 9-12 hours total.
#
# Before running on a fresh machine:
#   1. ``git pull origin main`` (this script must already be on disk)
#   2. Confirm ``--build`` path points at PackagedOutput_v2 demo1.exe
#   3. Optionally delete stray seed_0000_*_074843 dir under
#      harness_grid_iterate_offcenter_bboxv2 — that's the partial
#      white_grey_mug A from when this sweep was stopped.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

TASKS=(
  find_pan_cooktop_view              # 1
  find_pot_cooktop_view              # 2
  find_wooden_spoon_pot_view         # 3
  find_wooden_spatula_pot_view       # 4
  find_black_spoon_pot_view          # 5
  find_black_spatula_pot_view        # 6
  find_knife_cutting_view            # 7
  find_blue_mug_default_view         # 8
  find_white_grey_mug_default_view   # 9 (resume point in seed 0)
  find_coffee_mug_default_view       # 10
)

invoke() {
  local harness="$1" task="$2" chunk="$3" seed="$4" models extra
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  if [[ "$harness" == *grid_iterate_offcenter_bboxv2 ]]; then
    extra="--grid-n 3 --max-balls 1"
  else
    extra=""
  fi
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] harness=${harness##*.} seed=$seed task=$task chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task "$harness" \
    --task-id "$task" \
    --models "$models" \
    --seeds "$seed" \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 10 \
    --random-view \
    $extra \
    --log-level WARNING \
    || echo "  ⚠ chunk failed: ${harness##*.}/$task/$chunk seed=$seed — continuing"
  sleep 5
}

GRID3="veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate_offcenter_bboxv2"
GRID1="veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate_offcenter_bboxv2"

echo
echo "############################################################"
echo "# Phase 1: finish grid_iterate_offcenter_bboxv2 seed 0 (tasks 9-10)"
echo "############################################################"
for t in "${TASKS[@]:8}"; do
  invoke "$GRID3" "$t" A 0
  invoke "$GRID3" "$t" B 0
done

echo
echo "############################################################"
echo "# Phase 2: grid_iterate_offcenter_bboxv2 seed 1 (all 10 tasks)"
echo "############################################################"
for t in "${TASKS[@]}"; do
  invoke "$GRID3" "$t" A 1
  invoke "$GRID3" "$t" B 1
done

echo
echo "############################################################"
echo "# Phase 3: grid_iterate_offcenter_bboxv2 seed 2 (all 10 tasks)"
echo "############################################################"
for t in "${TASKS[@]}"; do
  invoke "$GRID3" "$t" A 2
  invoke "$GRID3" "$t" B 2
done

echo
echo "############################################################"
echo "# Phase 4: single_iterate_offcenter_bboxv2 seeds 0..2 (all tasks)"
echo "############################################################"
for seed in 0 1 2; do
  for t in "${TASKS[@]}"; do
    invoke "$GRID1" "$t" A "$seed"
    invoke "$GRID1" "$t" B "$seed"
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] bboxv2 dual-harness resume sweep done."
