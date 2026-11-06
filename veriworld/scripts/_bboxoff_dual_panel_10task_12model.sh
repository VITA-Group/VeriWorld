#!/usr/bin/env bash
# Dual bbox-marker ablation sweep — runs ``harness_grid_iterate_offcenter``
# (grid-3, 9-ball bbox scaffold) then ``harness_single_iterate_offcenter``
# (grid-1, 1-ball bbox) over the SAME models / seeds / tasks. Both
# harnesses now use the 12-edge wireframe axis-bbox marker (red X /
# green Y / blue Z edges + idx label at centre) instead of the prior
# sphere-with-3-arrows marker. The view_seed contract guarantees both
# variants see byte-identical camera poses for the same (task, seed)
# so cell-level diff isolates the grid-9 scaffold contribution.
#
# 6-instance batches × 2 chunks of 6 models — GPU has confirmed
# headroom for this on the dev box (RTX 3090, 24 GB VRAM).

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

HARNESSES=(
  veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate_offcenter
  veriworld.benchmark.photoreal_grounding.object_grounding.harness_single_iterate_offcenter
)

if [ "$#" -ge 1 ]; then SEEDS=("$@"); else SEEDS=(0 1 2); fi

invoke() {
  local harness="$1" task="$2" chunk="$3" seed="$4" models extra
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  # grid_iterate_offcenter still wants --grid-n 3 + --max-balls 1.
  # single_iterate_offcenter doesn't accept those (run_parallel filters
  # via inspect.signature so passing them is harmless, but keep them
  # consistent for log clarity).
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

for harness in "${HARNESSES[@]}"; do
  short="${harness##*.}"
  echo
  echo "############################################################"
  echo "# HARNESS $short — ${#SEEDS[@]} seeds, ${#TASKS[@]} tasks"
  echo "############################################################"
  for seed in "${SEEDS[@]}"; do
    for task in "${TASKS[@]}"; do
      invoke "$harness" "$task" A "$seed"
      invoke "$harness" "$task" B "$seed"
    done
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] bbox dual-harness sweep done."
