# Task: Surface Billiards — bounce shot

## Scene

A **green sphere at A** (fixed launch point, above the surface) and a
**red disc at B** (target crater on the surface) sit on a small bumpy
terrain (gaussian-bump landscape).

- A hill rises near A.
- A shallow crater at B is surrounded by four rim guards.
- Two small random bumps elsewhere.
- A broad flat bounce zone in the middle.

## Goal

Choose a horizontal aim and a launch speed so that the ball, launched
from A at a fixed downward pitch of -0.3 rad, flies, bounces off the
terrain, and **settles inside the red target crater at B**.

## What you control

| Parameter  | Range                 | Effect |
|------------|-----------------------|--------|
| `v_angle`  | radians (0 = +X axis) | horizontal direction of the shot |
| `v_speed`  | 0 – 350 cm/s          | how hard the ball is launched |

The **launch pitch is fixed** at -0.3 rad (slight downward). The **launch
point A is fixed**; you cannot move it.

## What you observe

Each round you are shown the **full mp4 video** of the previous
round — no pre-extracted frames, no grid. Watch the entire trajectory
to read where the ball flew, where it bounced, and where it ended up
relative to the red target B.

Round 1 sees the static observation flyover instead of a shot video
(there's no shot yet). Use it to read the A/B positions, the rough
direction to aim, and the terrain.

## How to think

Don't guess randomly. Build up understanding across rounds:

1. **Observation** — what happened in the video?
2. **Knowledge** — what do you now know about A→B direction, distance,
   the best speed, which angle overshoots, which undershoots?
3. **Plan** — pick the next `v_angle` / `v_speed` based on that
   knowledge, not on the previous guess alone.

## Response format

Reply with exactly these four blocks, in this order. The harness only
reads the last two as numbers; the first two are for your own
reasoning (saved for post-hoc audit).

```
observation: <what you saw in the frames>
knowledge: <everything you currently know about A/B, ranges, best params>
v_angle: <number in radians>
v_speed: <number in cm/s>
```

Example:

```
observation: Ball cleared the central hill but landed ~40cm short of B, slightly to the right.
knowledge: A is at roughly (-180, -120, 550); B looks ~300cm away in the +X,+Y direction. Pitch is fixed downward. Speed 220 is slightly too low; I need more range. Angle 52° is close but biases right — pull it left a bit.
v_angle: 0.87
v_speed: 240.0
```
