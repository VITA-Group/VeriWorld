"""lightwar.harness_combined — COMBINED (C) condition wrapper.

Delegates to :func:`harness_arc.task.run` with ``give_state=True,
give_images=True``: everything the S condition discloses (beacon apex
coordinates + symbolic shot telemetry) PLUS the launch-site preview and
per-shot FPV frame stacks. C = S + V by construction — the only delta
over harness_symbolic is the image channel, so the C-vs-S contrast
isolates visual-channel integration exactly as in the MovingShooter and
PlaceOnBottom panels.

Note: the pre-existing ``harness_arc`` (images + scalar miss, no
coordinates) is NOT the C condition — it remains the appendix
"V+Telemetry" ablation.
"""

from __future__ import annotations

from typing import Optional

from veriworld.common import RunLogger, VLMClient
from veriworld.infra.interactive.engine import InteractiveEngine

from ..harness_arc.task import run as _arc_run, EpisodeResult

__all__ = ["run", "EpisodeResult"]


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    task_id: str = "hit_forest_hilltop_beacon",
    max_rounds: Optional[int] = None,
    random_spawn: bool = False,
    view_seed: Optional[int] = None,
    intensity_profile: str = "medium",
    tower_height_jitter_cm: float = 0.0,
) -> EpisodeResult:
    return await _arc_run(
        engine, seed=seed, vlm=vlm, logger=logger,
        task_id=task_id, max_rounds=max_rounds,
        give_miss_distance=True,
        give_state=True,
        give_images=True,
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
