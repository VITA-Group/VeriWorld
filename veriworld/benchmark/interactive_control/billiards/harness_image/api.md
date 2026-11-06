# Agent API — billiards / harness_image

Same shot spec as the sibling `harness_video`. Differs only in WHAT
you receive each round: a single PNG screenshot of the **settled**
table after your previous shot — no rolling animation.

````
thought: <reasoning, plain text>

```json
{"angle_deg": <number>, "speed_cm_s": <number>}
```
````

| Field | Units | Range | Description |
|---|---|---|---|
| `angle_deg` | degrees | [-180, 180] | `0` = +X toward the rack, `+90` = +Y. |
| `speed_cm_s` | cm/s | (0, 1500] | Initial cue speed (clamped). |

## Constraints

- You only kick the **cue**.
- Cue position carries across rounds (UE is persistent), **except**
  on a scratch.
- **Cue scratch → respawn at table centre.** The cue is teleported
  back to the centre of the table with zero velocity; the round
  counts but you fire from centre next time. Episode does NOT end
  on a scratch.

## What's different from harness_video

You see only the **final** ball arrangement after each shot, not
how the balls got there. No information about rebound paths,
collision angles, or which ball hit which. Plan on positions alone.

This is the modality counterfactual: ablate "agent saw the
animation" vs "agent only saw the final layout" while holding
everything else (scene, physics, action space) constant.
