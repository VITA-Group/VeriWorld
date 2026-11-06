#!/usr/bin/env bash
# Living-room follow-up: 3 tasks (TV, planter, book) × 12 models, with
# the per-task `random_view` overrides in groundtruth.json so wall TV
# gets a near-horizontal pitch and 220-360 cm distance instead of the
# default counter-top top-down sampler.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"
BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'

TASKS=(
  find_tv_living_view
  find_planter_living_view
  find_book_living_view
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

for task in "${TASKS[@]}"; do
  invoke "$task" A
  invoke "$task" B
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all 3 LR tasks × 12 models done."
