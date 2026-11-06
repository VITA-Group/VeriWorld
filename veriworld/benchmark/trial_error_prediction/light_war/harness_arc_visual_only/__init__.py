"""lightwar — harness_arc_visual_only.

Same-everything ablation of ``harness_arc``: identical task, identical
analytic parabola model, identical 6-frame FPV stack feedback. The
ONLY difference is that the agent is **not** given a scalar miss
distance — neither in the per-round user message nor in the
``**Accumulated knowledge:**`` block. The agent must judge over /
under / left / right purely by reading the cyan beacon's position
relative to its projectile's flight path across the 6 stacked frames.

Use this against ``harness_arc`` to isolate how much of multi-round
convergence comes from the spotter feedback vs from genuine visual
reasoning over the FPV trajectory.
"""

from .task import run

__all__ = ["run"]
