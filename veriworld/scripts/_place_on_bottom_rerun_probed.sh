#!/usr/bin/env bash
# Re-run vision-harness cells that emitted at least one `probe`
# action under the BUGGY probe-restore (cube ended underground due
# to relative-restore reading the falling pose). Run AFTER the main
# sweep finishes; consumes the same UE build.
#
# Usage:
#   bash veriworld/scripts/_place_rerun_probed.sh
#
# Strategy: find every {model, seed} where vision-harness round_*_action.json
# contains "probe", group them by chunk so we can run all dirty
# cells of one (seed, chunk) together at 6-instance.

set -u
cd "$(dirname "$0")/../.."

CHUNK_A="claude-opus-4-5,claude-sonnet-4-6,gemini-2.5-flash,gemini-3.1-pro,gpt-4o,gpt-5-mini"
CHUNK_B="gpt-5.2,qwen3-vl-30b,qwen3-max,doubao-flash-1-6,kimi-k2.5,grok-4"

A_SET=",${CHUNK_A//,/,},"
B_SET=",${CHUNK_B//,/,},"

BUILD='${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1.exe'
HARNESS="veriworld.benchmark.interactive_control.place_on_bottom.harness_vision"

# --- Identify dirty cells via Python ---
DIRTY=$(python <<'PYEOF'
import json, re
from pathlib import Path
ROOT = Path(r"${VERIWORLD_ROOT}\veriworld\results\interactive_control\place_on_bottom\harness_vision")

def canonical(model):
    m = model
    for sfx in ('-20251101','-20251102','-20251022','-20240620','-preview','-thinking','-instruct','-a3b','-250828','-1-6'):
        m = m.replace(sfx, '')
    return m

dirty = set()  # (canonical_model, seed)
for panel in ROOT.glob("seed_*_2026*"):
    for cell in panel.iterdir():
        if not cell.is_dir(): continue
        # Did this cell emit a probe?
        probed = False
        for act in cell.glob("round_*_action.json"):
            try:
                d = json.loads(act.read_text(encoding='utf-8'))
                if d.get('cmd') == 'probe':
                    probed = True
                    break
            except Exception:
                pass
        if not probed: continue
        # Pull seed + model from episode_summary
        summ = cell / 'episode_summary.json'
        if not summ.exists(): continue
        try:
            s = json.loads(summ.read_text(encoding='utf-8'))
        except Exception:
            continue
        seed = s.get('seed')
        model = canonical(s.get('model', ''))
        if seed is None or not model: continue
        dirty.add((model, seed))

for m, s in sorted(dirty, key=lambda r: (r[1], r[0])):
    print(f"{m}|{s}")
PYEOF
)

if [[ -z "$DIRTY" ]]; then
  echo "no dirty cells found — nothing to re-run."
  exit 0
fi

echo "Dirty cells:"
echo "$DIRTY" | sed 's/^/  /'

# --- Group by seed; within a seed split by which chunk each model belongs to ---
declare -A SEEN_SEEDS
SEEDS=()
while IFS='|' read -r model seed; do
  if [[ -z "${SEEN_SEEDS[$seed]:-}" ]]; then
    SEEDS+=("$seed")
    SEEN_SEEDS[$seed]=1
  fi
done <<< "$DIRTY"

invoke_subset() {
  local seed="$1" chunk_name="$2" models_csv="$3"
  if [[ -z "$models_csv" ]]; then return; fi
  echo
  echo "============================================================"
  echo "RERUN_PANEL [$(date '+%H:%M:%S')] seed=$seed chunk=$chunk_name models=$models_csv"
  echo "============================================================"
  python -m veriworld.scripts.run_parallel \
    --task "$HARNESS" \
    --models "$models_csv" \
    --seeds "$seed" \
    --max-instances 6 \
    --build "$BUILD" \
    --max-rounds 8 \
    --log-level WARNING \
    || echo "  ⚠ rerun chunk failed: seed=$seed/$chunk_name — continuing"
  sleep 5
}

for seed in "${SEEDS[@]}"; do
  # Build A/B subsets of models that need re-running for this seed.
  a_subset=""; b_subset=""
  while IFS='|' read -r m s; do
    if [[ "$s" != "$seed" ]]; then continue; fi
    if [[ "$A_SET" == *",$m,"* ]]; then
      a_subset+="${a_subset:+,}$m"
    elif [[ "$B_SET" == *",$m,"* ]]; then
      b_subset+="${b_subset:+,}$m"
    fi
  done <<< "$DIRTY"
  invoke_subset "$seed" "A" "$a_subset"
  invoke_subset "$seed" "B" "$b_subset"
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] place_on_platform probe-rerun done."
