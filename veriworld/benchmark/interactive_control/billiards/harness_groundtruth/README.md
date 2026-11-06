# billiards — harness_groundtruth

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Pure-symbolic counterpart to `harness_visiongroundtruth`: the prompt
contains the **same** full physics ground truth (static config + per-
round dynamic positions) but **NO image** is attached. The model sees
only structured numbers.

- **Static scene parameters** (constant per episode): table dims,
  rail height/thickness, hole-corner gap, ball radius, cue and
  target ball linear/angular damping coefficients, cue speed cap,
  pocket centre coordinates.
- **Dynamic per-round state**: every alive ball's current
  (x, y, z), the alive count, and a `cue_scratched_last_round`
  flag.

The post-shot screenshot is still *harvested* to `round_NN.png` for
human inspection — but it never reaches the model.

This is the **vision-isolation** ablation: the score gap between
`harness_visiongroundtruth` and `harness_groundtruth` measures
exactly how much vision contributes when complete symbolic state is
already available.

| Harness | Image attached | Structured info |
|---|---|---|
| `harness_image` | settled PNG | (none) |
| `harness_visionposition` | settled PNG | per-ball positions |
| `harness_visiongroundtruth` | settled PNG | positions + every static physics param |
| **`harness_groundtruth`** | **(no image)** | positions + every static physics param |

## State memory

- **Who owns it?** UE (in-memory ball physics state, persistent
  across rounds in the same seed). The scene dumps a snapshot to
  `workspace/state.json` after each round's harvest.
- **Format**: JSON on disk, plain-text listing in prompt.
- **Update mechanism**: deterministic Chaos physics + scene
  tick-task harvest.
- **Fed back to agent in next step?** yes — full state + full
  static params text only (no image).

## LLM call budget

Per round: **1** VLM call (text-only — no `image_url` part).
Lower wall-clock than visiongroundtruth because no base64 image is
shipped over the wire.

## Determinism

- Prompt construction: deterministic given `params.json` (frozen
  at episode start) and `state.json`.
- State update: deterministic — Chaos physics; cue-respawn rule on
  scratch is rule-based.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = full ground truth, NO image**, **action = JSON shot spec** | Pure-symbolic billiards; agent gets full static + dynamic state, no PNG; outputs `{"angle_deg", "speed_cm_s"}`. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene code via `_shared.py`; only the prompt assembly
  differs from sibling `harness_visiongroundtruth` (image dropped
  from `messages` parts).
- Screenshot is still harvested via `harvest_screenshot(...)` so
  human reviewers can replay episodes; it just never enters the
  model's input.
- `state.json` is written by `_shared.py::_harvest` for every
  harness; this harness reads it AND `params.json` to build the
  full ground-truth block.
- Pocket centres are computed in task.py from `table_center_xy`
  + `table_size_x/y` + `hole_corner_gap` (geometry constant).
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_groundtruth.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
