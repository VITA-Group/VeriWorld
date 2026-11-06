# Agent API — billiards / harness_querytool

Same scene + same final action space as `harness_visiongroundtruth`,
**plus** a per-round REPL that lets you probe candidate shots
before committing.

You start the round seeing the same prompt as `visiongroundtruth`
(image + every static physics param + every alive ball's exact
(x, y, z)).  You then have **up to N "simulate" queries** before
you must commit a real shot.

## Action shapes

Each turn within a round, return ONE of these JSON objects:

### (a) Query — try a hypothetical shot, scene state is rolled back

````
thought: <reasoning>

```json
{
  "action": "query",
  "tool": "simulate_shot",
  "args": {"angle_deg": <number>, "speed_cm_s": <number>}
}
```
````

The harness runs the same Chaos physics that the real shot would
use, captures the outcome, then **teleports every ball back to its
pre-simulate position with zero velocity**.  Costs nothing in the
real game — only a turn of your simulate budget.

You will receive back, as the next user message:

```json
{
  "ok": true,
  "shot": {"angle_deg": ..., "speed_cm_s": ...},
  "duration_s": 2.4,
  "pocketed_balls": ["ball_3"],
  "cue_pocketed": false,
  "first_cue_contact": "ball_3",
  "ball_final_pos": {"cue": [x, y, z], "ball_0": [...], ...},
  "events": ["t=0.42 cue→ball_3", "t=0.71 ball_3_pocketed", ...]
}
```

`first_cue_contact` is whichever ball the cue touched first (or
`null` if it bounced off rails before hitting any ball).
`events` is a small chronological log for debugging *why* a shot
did or did not work.

### (b) Shoot — commit

````
thought: <reasoning>

```json
{
  "action": "shoot",
  "shot": {"angle_deg": <number>, "speed_cm_s": <number>}
}
```
````

This kicks the cue ball for real, runs full physics, advances
the round.  You may only issue ONE shoot per round.

Field ranges:

| Field | Units | Range |
|---|---|---|
| `angle_deg` | degrees | [-180, 180]; `0` = +X (toward the rack) |
| `speed_cm_s` | cm/s | (0, 1500] (clamped) |

## Budget

* `max_simulates_per_round` (default **3**) hypothetical
  `simulate_shot` queries before you are forced to commit.
* The harness tells you `remaining_simulates: K` in every prompt.
  When it reaches 0, the next message will be REQUIRED to be a
  `shoot` — a `query` will be rejected.

## Why this harness

`harness_visiongroundtruth` already gives you all the numbers,
so any further "structured info push" can't beat it — query would
only be a subset of push.

`harness_querytool` instead gives you a **strict capability gain
on top of** visiongroundtruth: the same complete information PLUS
the ability to probe the actual physics simulator before
committing.  This is information you *cannot* derive from the
push: forward-rolling Chaos dynamics is closed-form unsolvable.

The science question is: can the model use the simulator
productively?  Each round records `simulates_used` for analysis.

## Constraints (same as siblings)

- You only kick the **cue**.
- Cue position carries across rounds, **except** on a real-shot
  scratch (the cue is teleported back to the table centre and the
  episode continues).
- Simulates do NOT affect the real cue position — even if a
  simulate scratches, real state is restored.

## How it differs from sibling harnesses

| Harness | Visual | Structured info | Simulate tool | VLM calls/round |
|---|---|---|---|---|
| `harness_image` | settled PNG | (none) | – | 1 |
| `harness_visionposition` | settled PNG | agent-queried positions, next round | – | 1 |
| `harness_visiongroundtruth` | settled PNG | every static + dynamic param pushed | – | 1 |
| **`harness_querytool`** | settled PNG | every static + dynamic param pushed | ✓ (this one) | 1 + up to `max_simulates_per_round` |
