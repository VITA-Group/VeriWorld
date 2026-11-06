# billiards — harness_visiongroundtruth

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Same scene + same shot action space as `harness_image`, but each
round's prompt is enriched with the **full** physics ground truth:

- **Static scene parameters** (constant per episode): table dims,
  rail height/thickness, hole-corner gap, ball radius, cue and
  target ball linear/angular damping coefficients, cue speed cap,
  pocket centre coordinates.
- **Dynamic per-round state**: every alive ball's current
  (x, y, z), the alive count, and a `cue_scratched_last_round`
  flag.

The agent gets BOTH the visual and a complete numerical readout —
no perception bottleneck on positions, no physics-parameter
inference required.

This is the **maximal-info tier** of an ablation along
"how much structured ground-truth accompanies the image":

| Harness | Visual | Structured |
|---|---|---|
| `harness_image` | settled PNG | (none) |
| `harness_visionposition` | settled PNG | per-ball positions |
| `harness_visiongroundtruth` | settled PNG | positions + every static physics param |

## State memory

- **Who owns it?** UE (in-memory ball physics state, persistent
  across rounds in the same seed). The scene dumps a snapshot to
  `workspace/state.json` after each round's harvest.
- **Format**: JSON on disk, plain-text listing in prompt.
- **Update mechanism**: deterministic Chaos physics + scene
  tick-task harvest.
- **Fed back to agent in next step?** yes (PNG + full state +
  full static params).

## LLM call budget

Per round: **1** VLM call (policy via `vlm.chat` with an
`image_url` part + the textual ground-truth listing).

## Determinism

- Prompt construction: deterministic given the prior round's PNG,
  `params.json` (frozen at episode start), and `state.json`.
- State update: deterministic — Chaos physics; cue-respawn rule on
  scratch is rule-based.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = image + full ground truth**, **action = JSON shot spec** | Agent sees the prior shot's PNG plus full static + dynamic state in the prompt; outputs `{"angle_deg", "speed_cm_s"}`. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene code via `_shared.py`; only the prompt assembly
  differs from `harness_image` and `harness_visionposition`.
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

python -m veriworld.benchmark.interactive_control.billiards.harness_visiongroundtruth.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
