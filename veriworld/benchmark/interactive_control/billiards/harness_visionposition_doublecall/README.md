# billiards — harness_visionposition_doublecall

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Each round splits into **two sequential VLM calls** — same scene
state between them, no shot applied until Call 2 returns:

1. **Call 1 (vision-only strategy)**: image + scoreboard. Agent
   returns intent text + a list of ball IDs whose (x, y, z) it
   needs.
2. **Call 2 (text-only computation)**: agent's own Call-1 intent +
   the queried coordinates + a small static-physics block. NO
   image. Agent returns the final cue shot.

This is the **active-query, in-round** ablation: the latency that
`harness_visionposition` introduced (queries answered next round)
is gone, and the image is hidden during the numeric step so the
model can't silently re-do vision when given coordinates.

## State memory

- **Who owns it?** UE for ball physics. Per-round files under
  `workspace/round_NN_*` — Call-1 prompt + response, Call-2 prompt
  + response, query results JSON, the assembled shot, the verify
  log, and the post-shot screenshot.
- **Format**: JSON on disk; plain-text injected into prompts.
- **Update mechanism**: Chaos physics + scene tick-task harvest;
  query results pulled from `state.json` between Call 1 and Call 2.
- **Fed back to agent in next step?** yes — the screenshot for
  next round's Call 1; positions are rederived per round (not
  persisted across rounds).

## LLM call budget

Per round: **2** VLM calls. 2× the cost of single-call siblings.

## Determinism

- Prompt construction: deterministic given `state.json`,
  `params.json`, the prior round's PNG, and the agent's own
  Call-1 output.
- State update: deterministic — Chaos physics; cue-respawn rule
  on scratch is rule-based.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = image (Call 1) + queried positions (Call 2)**, **action = JSON shot spec** | Two sequential VLM calls; intent declared on the image, shot computed on the numbers. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene code via `_shared.py`; only the agent loop differs.
- Two parsers in task.py: `extract_call1_plan(...)` for
  `{intent, query_balls}` and `extract_call2_shot(...)` for the
  shot dict.
- Each VLM call is wrapped in a 3-retry exponential-backoff loop
  (`_vlm_with_retry`) so transient aggregator 400s don't burn a
  round.
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_visionposition_doublecall.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
