# moving_shooter

Predict `fire_time`, `aim_yaw`, `aim_pitch` for an orbiting shooter to
hit a moving target.

## Scene

* A blue target ball sits on a gray pillar. Both move together: the
  target orbits in a circle in XY and oscillates in Z; the pillar
  follows the target's XY so the ball appears to ride on top.
* A green arm orbits the scene at a larger radius with its own
  radial + Z oscillation, at a constant angular speed. Before fire
  the arm tracks the target's current direction; after fire it locks
  to the agent's chosen yaw/pitch.
* A red projectile spawns at the arm tip when fired. Horizontal
  speed decays exponentially. Vertical motion has gravity.

## Action space (per round)

* `fire_time` ∈ [0, 10] seconds — when (in scene time) to fire
* `aim_yaw` ∈ R — absolute world-frame horizontal direction (degrees)
* `aim_pitch` ∈ R — absolute barrel tilt (degrees, 0 = horizontal)

## Verdict

`HIT` if the projectile passes within `hit_radius` cm of the target
center at any tick. `MISS` otherwise (including stall: projectile
slower than `proj_stall_speed` cm/s).

## Coordinate-grounding twist (load-bearing for symbolic models)

The motion equations exposed to the symbolic / combined harnesses
are written in a ROTATED frame. The world frame used by the
simulation is rotated by a hidden angle (40-140°) relative to the
equation frame. A model that solves only from equations and
ignores the visual scene will compute an `aim_yaw` in the equation
frame that does NOT match world-frame yaw — so it will MISS.
Visual grounding (matching the equation-frame phase to where the
target *actually* is on screen) is what fixes this.
