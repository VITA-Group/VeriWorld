#!/usr/bin/env bash
# lightwar dual-harness sweep — runs ``harness_arc`` (with scalar
# miss-distance feedback) and ``harness_arc_visual_only`` (no scalar
# feedback) over the same models / seeds / tasks. The view_seed
# contract guarantees identical (launch, beacon XY, beacon colour,
# beacon intensity) for the same (task, seed) across BOTH harnesses,
# so cell-level diff isolates the effect of giving the model a
# numerical miss distance vs forcing it to read direction purely
# from the FPV stack.
#
# 6-instance / 2 chunks of 6 models — uses the full 12-model panel
# in two batches per (harness, seed). At ~3.6 GB VRAM/instance on
# the forest scene, 6 instances ≈ 21.6 GB / 24 GB on a 4090; close
# to the limit but workable. If CUDA OOM appears, drop max-instances.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-3.1-pro,gpt-5.2,gpt-5-mini,gpt-4o"
CHUNK_B="gemini-2.5-flash,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

if [ "$#" -ge 1 ]; then SEEDS=("$@"); else SEEDS=(0 1 2); fi

TASKS=(hit_forest_hilltop_beacon)
HARNESSES=(
  harness_arc
  harness_arc_visual_only
)

invoke() {
  local harness="$1" task="$2" chunk="$3" seed="$4" models
  case "$chunk" in
    A) models="$CHUNK_A" ;;
    B) models="$CHUNK_B" ;;
  esac
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] harness=$harness seed=$seed task=$task chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task "veriworld.benchmark.trial_error_prediction.light_war.${harness}" \
    --task-id "$task" \
    --models "$models" \
    --seeds "$seed" \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 8 \
    --random-spawn \
    --intensity-profile random \
    --height-jitter-cm 200.0 \
    --log-level WARNING \
    || echo "  ⚠ chunk failed: $harness/$task/$chunk seed=$seed — continuing"
  sleep 5
}

for harness in "${HARNESSES[@]}"; do
  echo
  echo "############################################################"
  echo "# HARNESS $harness — ${#SEEDS[@]} seeds, ${#TASKS[@]} tasks"
  echo "############################################################"
  for seed in "${SEEDS[@]}"; do
    for task in "${TASKS[@]}"; do
      invoke "$harness" "$task" A "$seed"
      invoke "$harness" "$task" B "$seed"
    done
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] lightwar dual-harness sweep done."
