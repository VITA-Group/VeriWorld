# billiards — harness_visionposition

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Same scene + same shot action space as `harness_image`, but each
round's prompt is enriched with the **per-ball ground-truth
(x, y, z) world position** (read from the scene's `state.json`
dump) — alongside the screenshot.

The agent gets BOTH the visual and a structured numerical listing
of where every alive ball currently sits. No other ground-truth
side info; the static physics parameters (table dims, damping,
etc) are NOT exposed.

This is the **middle tier** of an ablation along
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
- **Fed back to agent in next step?** yes (PNG + positions).

## LLM call budget

Per round: **1** VLM call (policy via `vlm.chat` with an
`image_url` part + the textual position listing).

## Determinism

- Prompt construction: deterministic given the prior round's PNG
  and `state.json`.
- State update: deterministic — Chaos physics; cue-respawn rule on
  scratch is rule-based.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = image + positions**, **action = JSON shot spec** | Agent sees the prior shot's PNG plus a `## Ball positions` block listing every alive ball's (x, y, z); outputs `{"angle_deg", "speed_cm_s"}`. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene code via `_shared.py`; only the prompt assembly
  differs from `harness_image`.
- `state.json` is written by `_shared.py::_harvest` for every
  harness — but only `harness_visionposition` and
  `harness_visiongroundtruth` actually read it. `harness_image`
  ignores it.
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`; no per-harness physics tuning.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_visionposition.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
