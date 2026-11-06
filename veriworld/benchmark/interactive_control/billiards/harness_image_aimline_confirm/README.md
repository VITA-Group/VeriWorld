# billiards — harness_image_aimline_confirm

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Built on top of `harness_image` (vision-only — settled PNG, NO
structured ground truth) **plus** a per-round visual-confirmation
REPL: every shot the agent proposes first gets drawn as a thin red
beam emanating from the cue ball at the chosen angle, and the agent
must `confirm` or `adjust` based on that visual preview before the
real shot fires.

Pairs with [`harness_aimline_confirm`](../harness_aimline_confirm/README.md)
(the visiongroundtruth-based variant) to isolate the contribution
of structured info under aim-line preview:

| Base modality | + aim-line REPL? | Harness |
|---|---|---|
| vision-only | no | `harness_image` |
| vision-only | **yes** | **`harness_image_aimline_confirm`** (this one) |
| vision + full GT | no | `harness_visiongroundtruth` |
| vision + full GT | **yes** | `harness_aimline_confirm` |

The 2×2 lets us answer two questions:
1. Does the aim-line preview help a model that has **no** numeric
   state to fall back on?  (this minus `harness_image`)
2. Does it add anything when the model already has every coordinate?
   (`harness_aimline_confirm` minus `harness_visiongroundtruth`)

## Per-round protocol

```
Turn 0: image-only prompt          → model returns proposed shot
Turn 1: image with aim line @ A_0  → confirm? or adjust?
Turn 2 (if adjust): image with aim line @ A_1 → confirm? or adjust?
...
Up to max_adjusts_per_round (default 3) adjustment turns.
After confirm OR exhausting budget: aim line cleared, real shot fires.
```

## State memory

- **Who owns it?** UE for ball physics. Aim-line actor under the
  label `BILL_aim_line` — never collides, always cleared before the
  real shot.
- **Format**: per-turn aim-line PNGs (`round_NN_aim_MM.png`) + post-
  shot PNG (`round_NN.png`). No `state.json` info goes into the
  prompt — this harness is image-only on purpose.
- **Update mechanism**: deterministic Chaos physics for the real
  shot; aim-line preview is a pure visual overlay.
- **Fed back to agent in next step?** within-round: the latest
  aim-line image is appended to the conversation; across rounds:
  post-shot PNG only (no GT push).

## LLM call budget

Per round: **1 + up to `max_adjusts_per_round`** VLM calls.
Default cap = 3 → worst case 4 calls/round (4× standard
harness_image cost).

## Determinism

- Prompt construction: deterministic given the prior round's PNG +
  the within-round REPL history.
- Aim-line spawn: deterministic — fixed length (1500 cm) and
  thickness; only the angle varies. Cleared before any real shot.
- State update: deterministic Chaos physics for the real shot;
  forced-fire flag set when REPL completes without an explicit
  confirm (logged for analysis).

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = image only + aim-line preview**, **action = proposal → confirm/adjust** | Multi-turn REPL within each round; agent proposes from image, sees aim-line overlay, commits or re-proposes (up to N times). |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene + aim-line code via `_shared.py`
  (`SCENE_SETUP_CODE`, `SHOT_TICK_CODE`, **`AIM_LINE_CODE`** /
  `AIM_LINE_CLEAR_CODE`).
- Per-turn artefacts: `round_NN_turn_MM_prompt.txt` /
  `round_NN_turn_MM_response.txt`. Aim-line previews:
  `round_NN_aim_MM.png`. Per-round summary: `round_NN_final_shot.json`,
  `round_NN.png` (post-shot), `round_NN_verify.txt`.
- Two parsers in `_shared.py`: `extract_shot` for the initial
  proposal (sibling-compat), `extract_aimline_decision` for the
  REPL turns (`{confirm: true}` / `{adjust: {...}}` / either of
  the `action`-tagged forms).
- VLM calls wrapped in 3-retry exponential-backoff with empty-
  response detection (gemini-3-flash sometimes returns empty
  content under multi-image prompts).
- Image-context budget: when more than one aim-line image
  accumulates in the conversation, earlier aim-line images are
  stripped to text placeholders so per-call image count stays at 2.
- `forced_fire` flag set when REPL exits without an explicit
  confirm (VLM exhausted or response unparseable). Force-fired
  rounds still execute the current proposal so a single bad call
  doesn't waste an entire round; downstream analysis can split
  model-confirmed vs harness-forced shots.
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_image_aimline_confirm.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
