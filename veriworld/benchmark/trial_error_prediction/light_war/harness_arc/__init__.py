"""lightwar — harness_arc.

Iterative arc-projectile grounding. Each round the agent sees the
6-frame FPV stack of its previous shot (or the launch-site preview on
round 1) and emits launch parameters (yaw, pitch, v0). The harness
analytically traces the parabola in forest space, captures 6 frames
along the projectile's path, stitches them into one composite image,
and feeds that back as the round's experience.
"""

from .task import run

__all__ = ["run"]
