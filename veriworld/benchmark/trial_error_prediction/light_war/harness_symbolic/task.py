"""lightwar.harness_symbolic — STRUCTURED (S) condition wrapper.

Delegates to :func:`harness_arc.task.run` with ``give_state=True,
give_images=False``: the prompt discloses the beacon apex coordinates
and full symbolic shot telemetry (landing, miss vector, arc samples
with raycast ground heights), and carries NO images. The residual
difficulty is terrain interception — the global heightfield is never
disclosed, only samples along arcs actually flown.

This is the S condition the Table 3 LightWar row previously claimed
without a harness existing. The S-vs-V gap on this task quantifies
what perception is worth when the target is visually locatable but its
coordinates are withheld (V) versus disclosed (S).
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
        give_images=False,
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
