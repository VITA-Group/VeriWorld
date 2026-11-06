"""Batched placement control with the corresponding single-arm observations.

The retained configuration uses three rounds, six total actions, and up
to five actions per batch. See the release README for the single-arm budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .._episode import EpisodeResult
from .._episode_batch import run_episode_batch
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap

SYSTEM_PROMPT = """\
You are an agent in a 3D physics scene with these objects:

  * one PLATFORM - a LARGE FLAT BOX. scale_cm has the largest XY
    footprint (200-350 cm wide on each side) and Z height 50-150 cm.
    Color rgb â‰ˆ (1.0, 0.9, 0.1) (yellow-ish). `mesh` = "cube".
  * one CUBE - a small physics-enabled cube (sides 70-110 cm, all
    three scale_cm components equal). Color rgb â‰ˆ (0.1, 0.3, 1.0)
    (blue-ish). `mesh` = "cube".
  * 2-8 DISTRACTORS - `mesh` is "cube" OR "sphere"; sides 70-120 cm;
    vivid colors that are NEVER blue or yellow.

Every actor has a generic label OBJ_0, OBJ_1, ... You DO NOT receive
a screenshot - each round you get the symbolic state list:

```json
[
  {{"label": "OBJ_0", "position": [x, y, z], "scale_cm": [sx, sy, sz],
   "rgb": [r, g, b], "mesh": "cube"|"sphere"}},
  ...
]
```

Identify the CUBE and PLATFORM by combining color AND size/shape:
  - PLATFORM is the entry whose `scale_cm` has the largest XY product
    AND has yellow-ish rgb (â‰ˆ 1, 0.9, 0.1).
  - CUBE is the entry whose `mesh == "cube"` AND scale_cm has all
    three components nearly equal (within ~5 cm) AND has blue-ish
    rgb (â‰ˆ 0.1, 0.3, 1).
  - Distractor colors are guaranteed distinct from cube/platform, so
    color usually disambiguates. If a distractor happens to look
    color-close, fall back to scale_cm + mesh.

ACTION FORMAT - emit ONE JSON code fence per round containing an
ORDERED BATCH of 1-{batch_cap} actions, executed strictly in order:

```json
{{"actions": [
  {{"cmd": "probe",   "label": "OBJ_2"}},
  {{"cmd": "move_to", "label": "OBJ_2", "to": [x, y, z]}},
  {{"cmd": "stop"}}
]}}
```

  * probe   - optional sanity lift (restored after the round)
  * move_to - teleport the object to absolute world coords (cm);
              zeros velocity
  * stop    - end episode; physics settles, verifier runs; any
              actions after stop in the batch are ignored

For the symbolic channel, `probe` is rarely needed since the rgb +
scale already disambiguate. Compute the platform's top-center using
its scale + position, and `move_to` the cube there with z =
platform.z + scale_cm[2]/2 + cube_scale_cm[2]/2.

You have {max_rounds} rounds and a TOTAL budget of
{max_total_actions} actions across all rounds. Feedback (new state
list) arrives only AFTER a whole batch executes, so plan the batch
carefully. After moving the cube onto the platform, include a stop
action.

PASS CONDITION: cube's final XYZ within tolerance of platform top
(XY inside platform footprint, Z = platform_top + cube.side/2 Â± 5
cm).
"""


async def _symbolic_observation(engine, params, prev_shot: Optional[Path]):
    out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    state = parse_state(out)
    state = annotate_with_colors(state, params)
    txt = "Current symbolic state:\n```json\n" + json.dumps(state, indent=2) + "\n```"
    parts = [{"type": "text", "text": txt}]
    return parts, [], {"channel": "symbolic", "state": state}


async def run(engine, seed, vlm, logger, *, max_rounds: int = 3,
              max_total_actions: int = 6, batch_cap: int = 5) -> EpisodeResult:
    return await run_episode_batch(
        engine, seed, vlm, logger,
        harness_id="symbolic_batch",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_symbolic_observation,
        package=__package__,
        max_rounds=max_rounds,
        max_total_actions=max_total_actions,
        batch_cap=batch_cap,
    )


__all__ = ["run", "EpisodeResult"]
