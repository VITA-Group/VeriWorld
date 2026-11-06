#!/usr/bin/env bash
# Wait for the currently-running multi-seed panel to finish, then
# kick off the grid-density ablation. Polls every minute.

set -u
MULTISEED_LOG=$(ls -t /c/Users/anonuser/projects/VeriWorld/runs/grid_panel_multiseed_*.log 2>/dev/null | head -1)
if [ -z "$MULTISEED_LOG" ]; then
  echo "no multiseed log yet — sleeping 60s and retrying"
  sleep 60
  MULTISEED_LOG=$(ls -t /c/Users/anonuser/projects/VeriWorld/runs/grid_panel_multiseed_*.log 2>/dev/null | head -1)
fi
echo "[$(date '+%H:%M:%S')] chain waiting on $MULTISEED_LOG"
until grep -q "all seeds .* done" "$MULTISEED_LOG" 2>/dev/null; do
  sleep 60
done
echo "[$(date '+%H:%M:%S')] multiseed done — sleeping 30s for cleanup, then launching ablation"
sleep 30

ABL_LOG="${VERIWORLD_ROOT}/runs/grid_panel_ablation_$(date +%Y%m%d_%H%M%S).log"
echo "ablation log: $ABL_LOG"
nohup bash "${VERIWORLD_ROOT}/veriworld/scripts/_grid_panel_ablation.sh" > "$ABL_LOG" 2>&1 &
echo "ablation pid=$!"
