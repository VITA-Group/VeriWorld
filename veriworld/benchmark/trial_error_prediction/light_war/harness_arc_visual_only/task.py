"""lightwar.harness_arc_visual_only — visual-only ablation wrapper.

Delegates the heavy lifting to :func:`harness_arc.task.run` with
``give_miss_distance=False`` so we don't duplicate the full ~600-line
task body. Anything you'd want to tweak about the underlying flow
(prompt wording, raycast cadence, frame stacking, etc.) lives in
``harness_arc.task`` — this wrapper only flips the feedback flag and
relabels the run_id so the result dirs don't collide with the
distance-feedback runs.
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
        give_miss_distance=False,
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
