# Billiards — Agent Instructions

You are an agent playing a top-down billiards game on a rectangular
table using Unreal Engine's Chaos physics. Each round you choose
**one cue shot**: the direction and speed of the white ball. The
engine runs the shot to completion (all balls settle), records a
video, and hands it to you for the next round.

## Objective

Pocket as many of the coloured **target** balls as possible within
`max_rounds` rounds. The task PASSES when **all** coloured balls are
in pockets; otherwise the episode FAILS but the pocketed count is
the continuous score. The exact target count and table dimensions
for this episode are listed in the per-round prompt under the
scoreboard / static parameters block.

## How the game actually works (read this carefully)

You can ONLY apply velocity to the **white cue ball**. You cannot
move or push the coloured balls directly. The coloured balls only
move when the cue ball — or another moving ball it has set into
motion — collides with them.

So every shot you plan must answer **two** questions:

1. **Which coloured ball do I want to pocket?**
2. **At what angle and speed must the cue ball strike that target
   so the target then rolls into a corner pocket?**

Reasoning about (2) is the actual physics challenge — you have to
predict the **post-collision trajectory of the target ball**, not
the trajectory of the cue ball alone:

- A head-on cue → target collision sends the target straight along
  the cue's incoming line and stops the cue dead at the contact point.
- A glancing collision sends the target perpendicular to the line
  joining the two ball centres at impact — the cue itself deflects
  off that line at roughly a right angle to the target's path. So
  to pocket a target along direction *D* into a specific pocket,
  aim the cue's centre at a "ghost ball" position one ball-diameter
  behind the target along *D*.
- The cue's residual motion after the collision is what drives the
  next shot's starting position (or sends it into a pocket — see
  below).

A common failure mode is treating the cue ball's straight-line
trajectory as the thing to aim — that pockets a target only by
luck. Aim the cue at the contact point that produces the target's
desired exit direction.

## Scratching the cue (don't)

If your shot causes the **cue ball** itself to fall into a pocket,
the engine teleports the cue back to the centre of the table with
zero velocity. The round still counts toward `max_rounds`; you've
just wasted a shot AND lost whatever positional advantage you had.
**Scratching is always a bad outcome.** A well-judged shot has the
target rolling into a pocket while the cue itself stops on the
table — ideally near a useful position for the NEXT target.

## Scene (fixed within a difficulty preset)

- **Green pool table** with 4 wooden rails along the edges. Each
  corner has a gap wide enough for a ball to fall through — those
  gaps are the **pockets** (4 of them, NE/NW/SE/SW). Exact table
  dimensions are listed under static parameters in the prompt.
- **1 white cue ball** starting at the -X (left/short) end.
- **N coloured target balls** in a triangle rack at the +X end. The
  exact count, colour palette, and rack layout depend on difficulty
  (5 balls in a 1+2+2 triangle for `standard`, 10 balls in a full
  1+2+3+4 triangle for `hard`). Colours are in canonical order:
  red, blue, yellow, green, pink, orange, cyan, purple, brown, black.
  The rack is slightly jittered by seed.
- **4 dark disc markers** show pocket locations (visual only).
- Camera is locked top-down so +X is screen-up, +Y is screen-right.

## Physics behaviour

- Balls have linear + angular damping so they slow down naturally over
  ~3 seconds after a shot — **not** a frictionless ice rink.
- Ball-ball collisions conserve momentum via Chaos contact forces.
- A ball that reaches a corner gap falls off the edge and counts as
  **pocketed** (its Z drops below the table top).
- Pocketed balls are NOT respawned on the next round. The table
  carries state across rounds.

## What you control

Each round, respond with a JSON block:

```json
{"angle_deg": 15.0, "speed_cm_s": 900.0}
```

- `angle_deg` — direction of the cue shot in the table plane. `0` =
  along +X (toward the rack), positive rotates toward +Y (screen-
  right), negative toward -Y (screen-left). Range: [-180, 180].
- `speed_cm_s` — initial speed of the cue ball. Range: (0, 1500].
  Typical useful range is 500–1300. The agent cannot choose cue
  position — it stays where it was left after the previous shot.

## Response format

```
thought:
  - <which ball am I trying to pocket this round, and into which pocket?>
  - <where is the cue ball currently? where is the target?>
  - <what direction does the target need to travel to reach the
     pocket, and therefore where on the target must the cue strike?>
  - <how hard? — too soft and the target won't reach the pocket; too
     hard and the cue may follow the target into the same pocket
     (scratch)>

```json
{"angle_deg": <number>, "speed_cm_s": <number>}
```
```

## Round 0 (observation)

On Round 0 you receive a bird's-eye view of the table at rest with the
fresh rack (an mp4 in `harness_video`, or a PNG in `harness_image`).
No action is requested on Round 0. Use it to orient and plan your
opening break.

## State persistence (important)

The UE instance stays alive for all rounds within an episode. Balls
NEVER re-spawn between rounds — wherever they end up after your
shot is exactly where they sit at the start of the next round.
What you see in the previous round's feedback (video or image) is
the literal physical state your next shot will act on.

## Notes

- The engine runs a `settle-detect` tick task that freezes all balls
  once they have settled (vmax < 8 cm/s for 0.3 s, or vmax < 50 cm/s
  past the 10 s soft cap). What you see in the next round's
  feedback is exactly the position the next cue shot will act on —
  no drift between observation and action.
- Cue ball linear/angular damping is intentionally higher than the
  coloured balls' damping (0.15 vs 0.05): the cue slows in a few
  seconds after a strong shot, while target balls roll a long way
  once hit.
- A round still counts even if your shot does nothing useful (e.g.
  cue misses every target). Plan each shot deliberately — wild
  break-style hits use up rounds without making progress.
