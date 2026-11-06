"""scene_ground.harness_teleport — view-recovery via free-observer pose setting.

Sibling of ``harness_walk`` (collision-aware embodied variant). Both
consume the same ``view_seed`` so they see identical (target_pose,
spawn_pose) pairs for the same (level, seed); only the action space
differs:

- ``harness_walk``: ``walk(distance)`` + ``set_view`` + ``stop`` —
  agent must navigate around obstacles.
- ``harness_teleport`` (this): ``set_pose(X, Y, Z, yaw, pitch)`` +
  ``stop`` — agent teleports anywhere instantly, ignoring geometry.

The ablation isolates "does collision-aware embodiment hurt view
recovery vs. free pose specification?".
"""

from .task import run, EpisodeResult

__all__ = ["run", "EpisodeResult"]
