# Agent API — billiards / harness_visiongroundtruth

Same shot spec as the sibling `harness_image`. Differs in WHAT you
receive each round: the settled-table PNG **plus** the full
quantitative ground truth of the scene under
`## Static scene parameters` and
`## Live ball state` blocks in the prompt.

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

## What you receive in the prompt (vs sibling harnesses)

`harness_visiongroundtruth` gives you everything the scene knows:

**Static (constant per episode)** — read off `params.json`:
- `table_center_xy`, `table_size_x`, `table_size_y`, `table_top_z`
- `rail_height`, `rail_thickness`, `hole_corner_gap`
- `ball_radius`
- `cue_linear_damping`, `cue_angular_damping`
- `ball_linear_damping`, `ball_angular_damping`
- `cue_max_speed_cm_s`
- `pocket_centres` (world (x, y) for NE / NW / SE / SW corner pockets,
  derived from table dims + corner gap)

**Dynamic (per round)** — read off `state.json`:
- Every alive ball's current (x, y, z)
- `alive_target` count
- `cue_scratched_last_round` flag

You can therefore plan strictly numerically (e.g. compute
ghost-ball positions, compute exact pocketing angles, predict
trajectories under the documented damping). The image is provided
mostly for sanity-checking — the numbers are the source of truth.
