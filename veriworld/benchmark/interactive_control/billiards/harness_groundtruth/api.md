# Agent API — billiards / harness_groundtruth

Pure-symbolic ablation. Same shot spec as the sibling `harness_image`,
**no image attached** — the prompt only contains structured ground
truth: full static physics + every alive ball's exact (x, y, z).

````
thought: <numeric reasoning, plain text>

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

## What you receive in the prompt

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

## Why this harness exists

`harness_visiongroundtruth` gives you image + every number. This
harness gives you the same numbers but **no image**. The score gap
between the two isolates the contribution of vision: how much, if
any, does seeing the table help when you already have the exact
coordinates?

If models score the same → vision adds nothing on top of perfect
symbolic state. If they score worse here → vision provides
something the structured listing doesn't (spatial chunking, gestalt
geometry intuition, sanity-checking the numbers, etc.).

## How it differs from sibling harnesses

| Harness | Image attached | Structured ground truth |
|---|---|---|
| `harness_image` | settled PNG | (none) |
| `harness_visionposition` | settled PNG | agent-queried positions, next round |
| `harness_visiongroundtruth` | settled PNG | all positions + all static params |
| **`harness_groundtruth`** (this one) | **(no image)** | all positions + all static params |
