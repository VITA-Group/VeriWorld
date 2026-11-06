#!/usr/bin/env bash
# Run all 9 iterate tasks with gemini-3.1-pro-preview in single-ball
# mode. Two batches of 6 / 3 in parallel (each on its own UE port).
#
# Usage: bash veriworld/scripts/_grid_iterate_9tasks_gemini.sh

set -u
cd "$(dirname "$0")/../.."

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
)

run_batch() {
  # $@ = task names; each launches in background on its own port.
  echo
  echo "============================================================"
  echo "[$(date '+%H:%M:%S')] launching batch ($# tasks in parallel)"
  echo "============================================================"
  local pids=()
  local port=9003
  for task in "$@"; do
    echo "  port=$port  task=$task"
    python -m veriworld.benchmark.photoreal_grounding.object_grounding.harness_grid_iterate \
      --task "$task" \
      --model gemini-3.1-pro \
      --max-rounds 6 \
      --max-balls 1 \
      --grid-n 3 \
      --port "$port" \
      --no-cleanup \
      --log-level WARNING \
      > "/tmp/grid9_${task}.log" 2>&1 &
    pids+=($!)
    port=$((port + 1))
    sleep 1.5  # stagger UE launches a touch so they don't all stomp on disk at once
  done
  echo "  waiting on pids: ${pids[*]}"
  for p in "${pids[@]}"; do
    wait "$p" || true
  done
  echo "[$(date '+%H:%M:%S')] batch done"
  # Clean up UE between batches.
  taskkill //F //IM demo1.exe > /dev/null 2>&1 || true
  sleep 3
}

# Pre-clean before the first batch.
taskkill //F //IM demo1.exe > /dev/null 2>&1 || true
sleep 2

run_batch "${TASKS[@]:0:6}"
run_batch "${TASKS[@]:6:3}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all 9 tasks done."
