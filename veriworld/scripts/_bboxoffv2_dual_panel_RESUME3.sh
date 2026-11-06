#!/usr/bin/env bash
# Continuation of bboxv2 dual sweep — same plan as RESUME2 but with
# 6-instance / 2-chunks-of-6 (matching the original
# _bboxoffv2_dual_panel_10task_12model.sh layout). Faster than the
# 4-instance / 3-chunks-of-4 layout when no harness dev is happening
# in parallel.
#
# Already-completed cleanly (do NOT re-run):
#   harness_grid_iterate_offcenter_bboxv2 — all 3 seeds (60 chunks done)
#   harness_single_iterate_offcenter_bboxv2 — seed 0 done (20 chunks)
#   harness_single_iterate_offcenter_bboxv2 — seed 1 tasks 1-3 done
#       (pan, pot, wooden_spoon — wooden_spoon B has 5/6 episodes;
#       grok-4 timed out for that one cell, accept and move on)
#
# Remaining (THIS script picks up here):
#   single-ball bboxv2, seed 1, tasks 4-10 (wooden_spatula → coffee_mug)
#   single-ball bboxv2, seed 2, all 10 tasks
# = 17 tasks × 2 chunks = 34 panels ~ 3-4 hours.

set -u
cd "$(dirname "$0")/../.."

# 12 models split 2 ways. Same model split as the original sweep so
# the per-cell wall clock is consistent across the 3-seed run.
CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

TASKS=(
  find_pan_cooktop_view              # 1
  find_pot_cooktop_view              # 2
  find_wooden_spoon_pot_view         # 3
  find_wooden_spatula_pot_view       # 4 (resume here in seed 1)
  find_black_spoon_pot_view          # 5
  find_black_spatula_pot_view        # 6
  find_knife_cutting_view            # 7
  find_blue_mug_default_view         # 8
  find_white_grey_mug_default_view   # 9
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

SINGLE="veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate_offcenter_bboxv2"

echo
echo "############################################################"
echo "# Phase 1: single-ball bboxv2 seed 1 (resume from task 4 = wooden_spatula)"
echo "############################################################"
for t in "${TASKS[@]:3}"; do
  invoke "$SINGLE" "$t" A 1
  invoke "$SINGLE" "$t" B 1
done

echo
echo "############################################################"
echo "# Phase 2: single-ball bboxv2 seed 2 (all 10 tasks)"
echo "############################################################"
for t in "${TASKS[@]}"; do
  invoke "$SINGLE" "$t" A 2
  invoke "$SINGLE" "$t" B 2
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] bboxv2 RESUME3 (6-instance) done."
