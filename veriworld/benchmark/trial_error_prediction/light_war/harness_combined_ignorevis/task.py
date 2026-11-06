"""lightwar.harness_combined_ignorevis — C condition, told to ignore the image.

Third rung of the framing ladder (see ``harness_combined_neutral`` for the
second). Same system prompt as C, same launch-site preview and per-shot FPV
stacks attached, S's round wording — plus one added line per round telling the
model that the image is there and should be disregarded, because the target's
exact coordinates are already in the system prompt.

    C-ignore vs C-neutral  ->  can an instruction recover the C < S drop?

If a model that collapses under C returns to its S score here, the drop is
recoverable by prompting and is a framing effect. If it stays collapsed, the
image is displacing the computation whether or not the model is told to skip it.
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
        state_first="ignore",
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
