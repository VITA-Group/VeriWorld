"""lightwar.harness_symbolic_tight — S condition at a 5 m tolerance.

Secondary analysis, NOT a table row. The canonical 30 m tolerance was
calibrated for visual aiming; the structured condition — exact target
coordinates plus the analytic trajectory model — clears it on the first
or second shot (0.93 pass rate, 2026-07-27), so the headline S cell is
at ceiling and cannot rank models.

This variant keeps everything identical except ``tolerance_cm=500``
(roughly the beacon tower's own scale), which forces the agent to keep
refining until it truly solves the ballistics — including clearing
intervening terrain — instead of stopping as soon as it lands "nearby".

Re-scoring the 30 m episodes at 5 m would NOT be equivalent: the harness
early-stops the moment a shot is inside tolerance, so a model that
stopped at 18 m never had the chance to refine further. The tighter
target has to be given up front.
"""

from __future__ import annotations

from typing import Optional

from veriworld.common import RunLogger, VLMClient
from veriworld.infra.interactive.engine import InteractiveEngine

from ..harness_arc.task import run as _arc_run, EpisodeResult

__all__ = ["run", "EpisodeResult"]

TIGHT_TOLERANCE_CM = 500.0


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
        tolerance_cm=TIGHT_TOLERANCE_CM,
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
