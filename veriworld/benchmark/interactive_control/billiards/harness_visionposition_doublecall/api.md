# Agent API — billiards / harness_visionposition_doublecall

Each round splits into **two sequential VLM calls** — a strategic
vision step and a numerical computation step. The harness applies
NO shot until Call 2 returns; both calls see the same scene state.

## Call 1 — vision-only strategy

You receive: the settled-table screenshot + scoreboard. NO ball
coordinates yet.

You return: a short plain-text intent + a list of ball IDs whose
exact (x, y, z) you want for Call 2.

Format:
````
thought: <observations from the image>

```json
{
  "intent": "<one-to-three-sentence plan, e.g. \"pocket yellow into NW pocket on a thin cut\">",
  "query_balls": ["cue", "ball_2"]
}
```
````

`query_balls` may include any of `"cue"`, `"ball_0"` … `"ball_4"`.
Empty list = no positions injected into Call 2.

## Call 2 — text-only computation

You receive: your own Call-1 `intent` text + the (x, y, z) for the
balls you requested + a small block of static physics
(table dims, damping, pocket centres). **NO image.**

You return: the final cue shot.

Format:
````
thought: <numeric reasoning — show the geometry>

```json
{"shot": {"angle_deg": <number>, "speed_cm_s": <number>}}
```
````

Field ranges:

| Field | Units | Range |
|---|---|---|
| `shot.angle_deg` | degrees | [-180, 180]; `0` = +X (toward the rack) |
| `shot.speed_cm_s` | cm/s | (0, 1500] (clamped) |

## Why two calls per round

Single-call harnesses (`harness_visionposition`,
`harness_visiongroundtruth`) let the agent re-derive vision from
the image even when given the numbers. This harness:

- **Forces a strategy-first / arithmetic-second decomposition** —
  Call 1 is the only step where the image is in front of the
  model; Call 2 is pure number-crunching.
- **Avoids the visionposition latency** — query results come back
  the same round, not the next.
- **Tests whether the gain of structured ground truth comes from
  "I have the numbers" (visiongroundtruth) or from "I asked for
  the right ones and reasoned about them"** (this harness).

## Constraints (same as siblings)

- You only kick the **cue**.
- Cue position carries across rounds (UE is persistent), **except**
  on a scratch.
- **Cue scratch → respawn at table centre.** Episode does NOT end.

## What's different from sibling harnesses

| Harness | Visual feedback | Structured info | VLM calls/round |
|---|---|---|---|
| `harness_image` | settled PNG | (none) | 1 |
| `harness_visionposition` | settled PNG | agent-queried positions, **next round** | 1 |
| **`harness_visionposition_doublecall`** (this one) | settled PNG (Call 1 only) | agent-queried positions, **same round Call 2** | 2 |
| `harness_visiongroundtruth` | settled PNG | every static + dynamic param pushed | 1 |
