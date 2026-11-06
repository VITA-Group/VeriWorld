"""lightwar.harness_combined_neutral — C condition with NEUTRAL round framing.

Why this arm exists. The shipped C arm (``harness_combined``) differs from S in
two ways at once, not one: it attaches the image AND its round wording points at
the image ("Use it to estimate yaw ... cross-check your visual estimate against
them"), where S says "Compute your first launch parameters from the structured
state in the system prompt." So a C-vs-S gap could be caused by the image
channel or by the instruction that comes with it.

This arm removes the second difference. The system prompt is byte-identical to
C, the same launch-site preview and per-shot FPV stacks are attached, and the
per-round user text is the S arm's wording verbatim — the image is present but
never referred to.

    C-neutral vs S  ->  the image channel on its own.
    C-neutral vs C  ->  the instruction that C's framing carries.

See ``harness_combined_ignorevis`` for the third rung, which additionally tells
the model to disregard the attached image.
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
        state_first="neutral",
        random_spawn=random_spawn,
        view_seed=view_seed,
        intensity_profile=intensity_profile,
        tower_height_jitter_cm=tower_height_jitter_cm,
    )
