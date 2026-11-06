#!/usr/bin/env bash
# Resume the bbox dual-harness sweep from where the API quota outage
# stopped it (around 01:07 on 2026-05-04, mid-task in seed 1).
#
# Already-completed cleanly (do NOT re-run):
#   harness_grid_iterate_offcenter, seed 0, all 10 tasks      (20 chunks)
#   harness_grid_iterate_offcenter, seed 1, tasks 1-5         (10 chunks)
#   harness_grid_iterate_offcenter, seed 1, task 6 chunk A    (1 chunk)
#
# Remaining (THIS script picks up here):
#   harness_grid_iterate_offcenter, seed 1, task 6 chunk B    (1 chunk)
#   harness_grid_iterate_offcenter, seed 1, tasks 7-10        (8 chunks)
#   harness_grid_iterate_offcenter, seed 2, all 10 tasks      (20 chunks)
#   harness_single_iterate_offcenter, seeds 0..2, all tasks   (60 chunks)
#
# = 89 panel-units. ~5-7 min each → 8-10 hours total.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

# Full task ordering matches the original panel script.
TASKS=(
  find_pan_cooktop_view              # 1
  find_pot_cooktop_view              # 2
  find_wooden_spoon_pot_view         # 3
  find_wooden_spatula_pot_view       # 4
  find_black_spoon_pot_view          # 5
  find_black_spatula_pot_view        # 6 (chunk A done in seed 1, chunk B is the resume point)
  find_knife_cutting_view            # 7
  find_blue_mug_default_view         # 8
  find_white_grey_mug_default_view   # 9
  find_coffee_mug_default_view       # 10
)

invoke() {
  local harness="$1" task="$2" chunk="$3" seed="$4" models extra
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  if [[ "$harness" == *grid_iterate_offcenter ]]; then
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

GRID3="veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate_offcenter"
GRID1="veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate_offcenter"

echo
echo "############################################################"
echo "# Phase 1: finish grid_iterate_offcenter seed 1 (from task 6 chunk B)"
echo "############################################################"
# task 6 chunk B (black_spatula B), then tasks 7-10 A+B
invoke "$GRID3" "${TASKS[5]}" B 1
for t in "${TASKS[@]:6}"; do
  invoke "$GRID3" "$t" A 1
  invoke "$GRID3" "$t" B 1
done

echo
echo "############################################################"
echo "# Phase 2: grid_iterate_offcenter seed 2 (all 10 tasks)"
echo "############################################################"
for t in "${TASKS[@]}"; do
  invoke "$GRID3" "$t" A 2
  invoke "$GRID3" "$t" B 2
done

echo
echo "############################################################"
echo "# Phase 3: single_iterate_offcenter seeds 0..2 (all 10 tasks)"
echo "############################################################"
for seed in 0 1 2; do
  for t in "${TASKS[@]}"; do
    invoke "$GRID1" "$t" A "$seed"
    invoke "$GRID1" "$t" B "$seed"
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] resume sweep done."
