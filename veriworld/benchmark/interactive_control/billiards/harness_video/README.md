# billiards — harness_video

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Agent sees a **mp4 of the entire physics playback** between shots
— rolling, bouncing, collisions, settle — and responds with a JSON
shot spec. The harness uses `InteractiveEngine` (one persistent UE
per seed); balls never re-spawn between rounds, so where the cue
ended IS where the next shot fires from. No state.json round-trip.

The single axis this harness studies (vs. its sibling
`harness_image`) is **whether seeing the rolling animation helps**:
in the video harness the agent gets the full motion record; in the
image harness it gets only a still of the settled scene.

## State memory

- **Who owns it?** UE — the running level itself holds ball physics
  state between shots. The harness owns a small `# RESULT:` log line
  per round so the host can break early on cue-pocketed.
- **Format**: in-memory UE actors; verify log on disk per round.
- **Update mechanism**: deterministic — Chaos physics + scene
  tick-task harvest.
- **Fed back to agent in next step?** yes (as the prior round's
  mp4).

## LLM call budget

Per round: **1** VLM call (policy via `vlm.chat_with_video`). No
summarizer.

## Determinism

- Prompt construction: deterministic given the prior round's mp4 +
  scoreboard line.
- State update: deterministic — pure Chaos physics; no LLM in the
  state transition path.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = video**, **action = JSON shot spec** | Agent sees the prior shot's mp4, outputs `{"angle_deg", "speed_cm_s"}`. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. Async coroutine on
  `InteractiveEngine`; `attach`-mode (the user/orchestrator
  launches UE).
- Scene code (table, rails, balls, settle-detect, harvest) lives in
  the task-shared `_shared.py`; this harness only owns the
  `_common.py` helpers for `harvest_video` (h264 → mp4 via
  host-side ffmpeg, since `subprocess` is blacklisted in UELivePy).
- `rec_dir_from_exe(--exe)` derives the UE recording directory from
  the build path (InteractiveEngine doesn't expose `rec_dir`).
- Settle threshold: max linear velocity < 8 cm/s for 0.3 s after
  ≥0.8 s of elapsed time. Hard cap 10 s.

## Running (solo)

UE must already be running on the chosen port at 640×480:

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash
```

Then:

```
python -m veriworld.benchmark.interactive_control.billiards.harness_video.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash-video
```
