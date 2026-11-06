"""object_ground — harness_single_iterate.

Single-ball variant: round 1 spawns ONE marker at the camera optical
axis hit (no 9-ball grid scaffold). Agent nudges that one ball with
(dx, dy, dz) each round. Verifier same as grid_iterate.
"""

from .task import run

__all__ = ["run"]
