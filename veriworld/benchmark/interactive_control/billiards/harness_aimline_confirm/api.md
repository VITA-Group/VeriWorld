# Agent API — billiards / harness_aimline_confirm

Built on top of `harness_visiongroundtruth` (image + full ground
truth) **plus** a per-round visual-confirmation REPL: every shot
you propose first gets drawn as a red aim line on the cue ball,
and you must confirm or adjust based on that visual preview.

## Per-round protocol

Each round splits into **1 + up to N turns** (N = `max_adjusts_per_round`,
default 3):

### Turn 0 — initial proposal

You receive: settled-table screenshot + scoreboard + full static
physics + every alive ball's (x, y, z).

You return: a proposed cue shot.

````
thought: <reasoning>

```json
{"angle_deg": <number>, "speed_cm_s": <number>}
```
````

### Turn k (k = 1..N) — aim-line confirm/adjust

The harness draws a **red beam** from the cue ball along your
proposed angle, takes a screenshot, and asks you to confirm.

You receive: the same table with the red aim-line overlay, plus the
proposed `angle_deg` / `speed_cm_s` numbers.

You return ONE of:

(a) **Confirm** — fire the proposed shot as-is, end the round's REPL.

````
thought: <why the line looks right>

```json
{"confirm": true}
```
````

(b) **Adjust** — re-aim with a new angle (and optionally a new speed).
The beam will be redrawn, and you'll be asked again.

````
thought: <what was wrong + the correction>

```json
{"adjust": {"angle_deg": <number>, "speed_cm_s": <number>}}
```
````

After N adjustments without a confirm, the current proposal fires
anyway (the harness logs `forced_fire: true` for that round).

## Field ranges (same as siblings)

| Field | Units | Range |
|---|---|---|
| `angle_deg` | degrees | [-180, 180]; `0` = +X (toward the rack) |
| `speed_cm_s` | cm/s | (0, 1500] (clamped) |

## What the aim line shows

- **Direction only.** A red beam ~1500 cm long emanating from the
  cue ball center along `angle_deg`. Speed is **not** visualised
  (you choose it numerically).
- **No collision.** The beam doesn't push balls or block shots; it
  only exists in the screenshot you confirm against. It's cleared
  from the scene before the real shot fires.

## Why this harness

`harness_visiongroundtruth` already gives you image + every number.
Numerical aim errors (typed wrong sign, mis-converted radians,
ghost-ball geometry off by a digit) only become visible AFTER the
shot fires — when it's too late. This harness lets you **see your
own intended direction overlaid on the table** and reject obvious
mistakes before committing.

The score gap between `harness_aimline_confirm` and
`harness_visiongroundtruth` measures how much an explicit
visual-feedback loop helps when complete numbers are already
available — i.e. whether models can use a preview to catch the
errors their own reasoning produced.

## Constraints (same as siblings)

- You only kick the **cue**.
- Cue position carries across rounds, **except** on a real-shot
  scratch (cue is teleported back to the table centre and the
  episode continues).
- Aim-line previews never affect game state (the beam is destroyed
  before the real shot fires).

## How it differs from sibling harnesses

| Harness | Image | Structured info | Aim-line preview | VLM calls/round |
|---|---|---|---|---|
| `harness_image` | settled PNG | (none) | – | 1 |
| `harness_visionposition` | settled PNG | agent-queried positions, next round | – | 1 |
| `harness_groundtruth` | (no image) | every static + dynamic param | – | 1 |
| `harness_visiongroundtruth` | settled PNG | every static + dynamic param | – | 1 |
| **`harness_aimline_confirm`** | settled PNG + aim-line previews | every static + dynamic param | ✓ (this one) | 1 + up to N |
