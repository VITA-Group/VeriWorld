"""scene_ground.harness_walk — view-recovery via collision-aware walking.

Sibling of ``harness_teleport`` (free-observer no-collision variant).
Both consume the same ``view_seed`` so they see identical
(target_pose, spawn_pose) pairs for the same (level, seed).
"""

from .task import run, EpisodeResult

__all__ = ["run", "EpisodeResult"]
