"""place_on_platform — SYMBOLIC harness.

Per round, agent observation = a JSON list of every PLACE_OBJ_i with
its current world position, scale (cm), and rgb tint. NO screenshot.
The agent identifies the cube / platform from these numbers and
emits the standard probe / move_to / stop actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap

SYSTEM_PROMPT = """\
You are an agent in a 3D physics scene with these objects:

  * one PLATFORM — a LARGE FLAT BOX. scale_cm has the largest XY
    footprint (200-350 cm wide on each side) and Z height 50-150 cm.
    Color rgb ≈ (1.0, 0.9, 0.1) (yellow-ish). `mesh` = "cube".
  * one CUBE — a small physics-enabled cube (sides 70-110 cm, all
    three scale_cm components equal). Color rgb ≈ (0.1, 0.3, 1.0)
    (blue-ish). `mesh` = "cube".
  * 2-8 DISTRACTORS — `mesh` is "cube" OR "sphere"; sides 70-120 cm;
    vivid colors that are NEVER blue or yellow.

Every actor has a generic label OBJ_0, OBJ_1, ... You DO NOT receive
a screenshot — each round you get the symbolic state list:

```json
[
  {{"label": "OBJ_0", "position": [x, y, z], "scale_cm": [sx, sy, sz],
   "rgb": [r, g, b], "mesh": "cube"|"sphere"}},
  ...
]
```

Identify the CUBE and PLATFORM by combining color AND size/shape:
  - PLATFORM is the entry whose `scale_cm` has the largest XY product
    AND has yellow-ish rgb (≈ 1, 0.9, 0.1).
  - CUBE is the entry whose `mesh == "cube"` AND scale_cm has all
    three components nearly equal (within ~5 cm) AND has blue-ish
    rgb (≈ 0.1, 0.3, 1).
  - Distractor colors are guaranteed distinct from cube/platform, so
    color usually disambiguates. If a distractor happens to look
    color-close, fall back to scale_cm + mesh.

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{{"cmd": "probe",   "label": "OBJ_2"}}                   // optional sanity probe
{{"cmd": "move_to", "label": "OBJ_2", "to": [x, y, z]}}  // teleport to (x,y,z) cm; velocity zeroed
{{"cmd": "stop"}}                                         // end episode; physics settles, verifier runs
```

For the symbolic channel, `probe` is rarely needed since the rgb +
scale already disambiguate. Compute the platform's top-center using
its scale + position, and `move_to` the cube there with z =
platform.z + scale_cm[2]/2 + cube_scale_cm[2]/2.

PASS CONDITION: cube's final XYZ within tolerance of platform top
(XY inside platform footprint, Z = platform_top + cube.side/2 ± 5
cm).

You have {max_rounds} rounds.
"""


async def _symbolic_observation(engine, params, prev_shot: Optional[Path]):
    out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    state = parse_state(out)
    state = annotate_with_colors(state, params)
    txt = "Current symbolic state:\n```json\n" + json.dumps(state, indent=2) + "\n```"
    parts = [{"type": "text", "text": txt}]
    return parts, [], {"channel": "symbolic", "state": state}


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id="symbolic",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_symbolic_observation,
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult"]
