# Agent API — billiards / harness_video

Each round, respond with a `thought:` line followed by one JSON code block:

````
thought: <reasoning, plain text>

```json
{"angle_deg": <number>, "speed_cm_s": <number>}
```
````

| Field | Units | Range | Description |
|---|---|---|---|
| `angle_deg` | degrees | [-180, 180] | Direction of cue shot in the table plane. `0` = +X (toward the rack), `+90` = +Y (screen-right), `-90` = -Y (screen-left). |
| `speed_cm_s` | cm/s | (0, 1500] | Initial speed of the cue ball. Values are clamped to the range. |

## Constraints

- You can only kick the **cue** ball, never the target balls directly.
- Cue position carries between rounds — wherever the cue ended up
  after your previous shot is where it starts the next, **except**
  when the cue scratches.
- **Cue scratch → respawn at table centre.** The cue is teleported
  back to the centre of the table with zero velocity; the round
  counts but you fire from centre next time. Episode does NOT end
  on a scratch.

## Pre/post per round

**Before** (Round 0 only): table, rails, corner pocket markers, 5
coloured balls + cue all spawned. UE stays running across all
rounds — the scene is built once.

**Each round**: scene script looks up the cue actor by label,
applies your shot via `SetPhysicsLinearVelocity`, starts a 640×480
h264 recording, runs a settle-detect tick task. When all balls have
been below 8 cm/s for 0.3 s, balls freeze, recording stops. Any
coloured ball below `table_top - 10` is destroyed (counts as
pocketed). If the cue is below that threshold, it is teleported
back to `table_center_xy` with zero linear/angular velocity (cue
scratch — episode continues). `# RESULT:` is written to
`workspace/lean_verify/log_for_verify.txt`.

**Feedback to next round**: the recorded mp4 of THIS round's full
playback (rolling, bouncing, settling) is fed to your next prompt
via `vlm.chat_with_video`.

## Damping (asymmetric)

- Cue: linear + angular = 0.15 (decays in a few seconds after break).
- Coloured balls: linear + angular = 0.05 (roll long after being
  hit).
