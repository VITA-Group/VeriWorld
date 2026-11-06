#!/usr/bin/env bash
# 10-task batch: gemini-3.1-pro, single-ball, random-view, 6 rounds.
# Two batches of 6 / 4 in parallel on ports 9003+.
#
# Usage:
#   bash veriworld/scripts/_grid_iterate_10tasks_random.sh

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
  find_coffee_mug_default_view
)

run_batch() {
  echo
  echo "============================================================"
  echo "[$(date '+%H:%M:%S')] launching $# tasks in parallel"
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
      --random-view \
      --port "$port" \
      --no-cleanup \
      --log-level WARNING \
      > "/tmp/grid10r_${task}.log" 2>&1 &
    pids+=($!)
    port=$((port + 1))
    sleep 1.5
  done
  echo "  waiting on pids: ${pids[*]}"
  for p in "${pids[@]}"; do wait "$p" || true; done
  echo "[$(date '+%H:%M:%S')] batch done"
  taskkill //F //IM demo1.exe > /dev/null 2>&1 || true
  sleep 3
}

taskkill //F //IM demo1.exe > /dev/null 2>&1 || true
sleep 2
run_batch "${TASKS[@]:0:6}"
run_batch "${TASKS[@]:6:4}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] all 10 tasks done."
