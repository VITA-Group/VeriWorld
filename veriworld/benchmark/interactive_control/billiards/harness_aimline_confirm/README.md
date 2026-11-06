# billiards — harness_aimline_confirm

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Built on top of `harness_visiongroundtruth` (image + complete
static + dynamic ground truth) **plus** a per-round
visual-confirmation REPL: every shot the agent proposes first gets
drawn as a thin red beam emanating from the cue ball at the chosen
angle, and the agent must `confirm` or `adjust` based on that
visual preview before the real shot fires.

This is the **visual-feedback-loop** ablation: structured info is
held constant vs `visiongroundtruth`; the only added capability is
"see your own intended direction overlaid on the table before
committing". Numerical aim errors (sign flipped, ghost-ball
geometry off, radians-vs-degrees) become visible BEFORE the shot
fires instead of after.

## Per-round protocol

```
Turn 0: image + scoreboard + full GT  → model returns proposed shot
Turn 1: image with aim line @ angle_0 → confirm? or adjust?
Turn 2 (if adjust): image with aim line @ angle_1 → confirm? or adjust?
...
Up to max_adjusts_per_round (default 3) adjustment turns.
After confirm OR exhausting budget: aim line cleared, real shot fires.
```

## State memory

- **Who owns it?** UE for ball physics (persistent across rounds
  per seed). Aim-line actor is spawned/destroyed per turn under
  the label `BILL_aim_line` — never collides, never affects
  physics, always cleared before the real shot.
- **Format**: `state.json` (per-round dump, same as siblings) +
  per-turn aim-line PNGs (`round_NN_aim_MM.png`).
- **Update mechanism**: deterministic Chaos physics for the real
  shot; aim-line preview is purely visual overlay.
- **Fed back to agent in next step?** within-round: the latest
  aim-line image is appended to the conversation; across rounds:
  same as `visiongroundtruth` (post-shot PNG + full state push).

## LLM call budget

Per round: **1 + up to `max_adjusts_per_round`** VLM calls.
Default cap = 3 → worst case 4 calls/round (2× standard
visiongroundtruth cost). Caller may set
`max_adjusts_per_round=0` to disable the REPL entirely (collapses
to a `visiongroundtruth`-equivalent baseline that nominally exposes
the aim line UI but never asks for it — useful as a sanity-check
control).

## Determinism

- Prompt construction: deterministic given the prior round's PNG +
  `params.json` + `state.json` + the within-round REPL history.
- Aim-line spawn: deterministic — fixed length (1500 cm) and
  thickness; only the angle varies. Cleared before any real shot.
- State update: deterministic Chaos physics for the real shot.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = visiongroundtruth + aim-line preview**, **action = proposal → confirm/adjust** | Multi-turn REPL within each round; agent proposes a shot, sees the aim-line overlay, commits or re-proposes (up to N times). |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene + aim-line code via `_shared.py`
  (`SCENE_SETUP_CODE`, `SHOT_TICK_CODE`, **`AIM_LINE_CODE`** /
  `AIM_LINE_CLEAR_CODE`).
- Per-turn artefacts:
  `round_NN_turn_MM_prompt.txt`, `round_NN_turn_MM_response.txt`.
  Aim-line previews: `round_NN_aim_MM.png`. Per-round summary:
  `round_NN_final_shot.json`, `round_NN.png` (post-shot), and
  `round_NN_verify.txt`.
- Two parsers in `_shared.py`: `extract_shot` for the initial
  proposal (sibling-compat), `extract_aimline_decision` for the
  REPL turns (`{confirm: true}` / `{adjust: {...}}` / either of
  the `action`-tagged forms).
- VLM calls wrapped in 3-retry exponential-backoff (same as other
  siblings; doubly important here because call count per round is
  up to 4×).
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`. Aim-line is destroyed BEFORE every real shot so it
  never appears in post-shot screenshots.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_aimline_confirm.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
