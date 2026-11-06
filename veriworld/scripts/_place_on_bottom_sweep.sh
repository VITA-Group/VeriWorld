#!/usr/bin/env bash
# place_on_platform sweep — vision / symbolic / combined × 20 seeds × 12
# models. Layout: 6-instance / 2-chunks-of-6 (matches bboxv2 sweep
# pattern). 3 harnesses × 20 seeds × 2 chunks = 120 panels.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"

BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'
SEEDS="0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19"

HARNESSES=(
  "veriworld.benchmark.interactive_control.place_on_bottom.harness_vision"
  "veriworld.benchmark.interactive_control.place_on_bottom.harness_symbolic"
  "veriworld.benchmark.interactive_control.place_on_bottom.harness_combined"
)

invoke() {
  local harness="$1" seed="$2" chunk="$3" models
  if [ "$chunk" = "A" ]; then models="$CHUNK_A"; else models="$CHUNK_B"; fi
  echo
  echo "============================================================"
  echo "PANEL_TASK_START [$(date '+%H:%M:%S')] harness=${harness##*.} seed=$seed chunk=$chunk models=$models"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task "$harness" \
    --models "$models" \
    --seeds "$seed" \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 8 \
    --log-level WARNING \
    || echo "  ⚠ chunk failed: ${harness##*.}/seed=$seed/$chunk — continuing"
  sleep 5
}

for harness in "${HARNESSES[@]}"; do
  echo
  echo "############################################################"
  echo "# HARNESS ${harness##*.}"
  echo "############################################################"
  for seed in $SEEDS; do
    invoke "$harness" "$seed" A
    invoke "$harness" "$seed" B
  done
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] place_on_platform sweep done."
