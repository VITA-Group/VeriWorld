"""object_ground — harness_grid_iterate.

Round-1 seeds an N×N camera-grid of numbered balls so the agent has a
hard pixel ↔ world-coord correspondence to anchor on. Subsequent rounds
behave like ``harness_iterate``: the agent emits a full ball list and a
``closest_color`` claim, the harness despawns + respawns, and the
verifier checks distance(claimed-closest, target_alias) <= tolerance.
"""

from .task import run

__all__ = ["run"]
