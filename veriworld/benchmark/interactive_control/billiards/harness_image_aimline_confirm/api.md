# Agent API — billiards / harness_image_aimline_confirm

Built on top of `harness_image` (vision-only — settled PNG, NO
structured ground truth) **plus** a per-round visual-confirmation
REPL: every shot you propose first gets drawn as a red aim line on
the cue ball, and you must confirm or adjust based on that visual
preview.

## Per-round protocol

Each round splits into **1 + up to N turns** (N = `max_adjusts_per_round`,
default 3):

### Turn 0 — initial proposal

You receive: settled-table screenshot + scoreboard. **No numeric
ball positions, no physics constants** — vision only.

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
anyway and is flagged `forced_fire: true` in the trajectory log.

## Field ranges (same as siblings)

| Field | Units | Range |
|---|---|---|
| `angle_deg` | degrees | [-180, 180]; `0` = +X (toward the rack) |
| `speed_cm_s` | cm/s | (0, 1500] (clamped) |

## What the aim line shows

- **Direction only.** A red beam ~1500 cm long emanating from the
  cue ball center along `angle_deg`. Speed is **not** visualised.
- **No collision.** The beam doesn't push balls or block shots; it
  only exists in the screenshot you confirm against. It's cleared
  from the scene before the real shot fires.

## Why this harness

Pairs with `harness_aimline_confirm` (the vision + full-ground-truth
counterpart) to isolate the effect of structured info under aim-line
preview:

- `harness_image_aimline_confirm` (this one): pure vision-only +
  aim-line REPL. Tests whether the visual preview alone — without
  any numbers — lets a model self-correct.
- `harness_aimline_confirm`: vision + every coordinate + aim-line
  REPL. Tests whether the preview adds anything when the agent
  already has perfect symbolic state.

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
| **`harness_image_aimline_confirm`** (this one) | settled PNG + aim-line previews | (none) | ✓ | 1 + up to N |
| `harness_visiongroundtruth` | settled PNG | every static + dynamic param | – | 1 |
| `harness_aimline_confirm` | settled PNG + aim-line previews | every static + dynamic param | ✓ | 1 + up to N |
