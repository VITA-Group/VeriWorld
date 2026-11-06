# billiards — harness_image

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Sibling of `harness_video`. Identical scene, identical action space,
identical InteractiveEngine plumbing — the **only** difference is
that the per-round feedback is a single **PNG screenshot** of the
settled table after each shot, NOT a recording of the physics
playback.

The axis we vary across these two harnesses is **feedback
modality**: video vs single image. Comparing PASS rates (and
shot-quality trajectories) between the two on the same model + same
seed isolates how much the rolling-animation channel contributes
beyond the final layout.

## State memory

- **Who owns it?** UE (in-memory ball physics state). Same as the
  video sibling.
- **Format**: in-memory; verify log per round.
- **Update mechanism**: deterministic Chaos physics.
- **Fed back to agent in next step?** yes (as the prior round's
  PNG, captured via `engine.screenshot()`).

## LLM call budget

Per round: **1** VLM call (policy via `vlm.chat` with an
`image_url` part). No summarizer.

## Determinism

- Prompt construction: deterministic given the prior round's PNG +
  scoreboard line.
- State update: deterministic — Chaos physics.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = image**, **action = JSON shot spec** | Agent sees the prior shot's settled PNG, outputs `{"angle_deg", "speed_cm_s"}`. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Scene code shared with `harness_video` via the task-level
  `_shared.py`. The shot script's `__MODALITY__` placeholder is
  set to `"image"` here so it skips StartRecording / StopRecording
  — only runs the settle-detect tick task.
- `harvest_screenshot` triggers an `engine.screenshot()` after
  settle, polls `<build>/Windows/demo1/Saved/Screenshots/Windows`
  for the new png, copies it into the workspace.
- Same settle thresholds + cue-pocket-FAIL semantics as
  `harness_video`.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_image.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```

(Note: `harness_image` doesn't strictly need a video-capable model
— any chat-with-image model works. `gemini-2.5-flash` non-video
variant is sufficient.)
