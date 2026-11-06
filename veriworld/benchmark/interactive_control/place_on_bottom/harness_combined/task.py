"""place_on_platform — COMBINED harness.

Per round, agent observation = current screenshot AND a JSON list of
every PLACE_OBJ_i's symbolic state. Action set is the standard
probe / move_to / stop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from veriworld.common import png_to_base64_url

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, annotate_with_colors, parse_state, unwrap

SYSTEM_PROMPT = """\
You are an agent in a 3D physics scene with these objects:

  * one PLATFORM — a LARGE FLAT BOX (footprint 200-350 cm × 200-350
    cm, height 50-150 cm). Yellow-ish rgb ≈ (1.0, 0.9, 0.1). The
    largest + flattest object in the scene. `mesh` = "cube".
  * one CUBE — a small physics-enabled cube (equal sides 70-110 cm).
    Blue-ish rgb ≈ (0.1, 0.3, 1.0). `mesh` = "cube".
  * 2-8 DISTRACTORS — `mesh` is "cube" OR "sphere", sides 70-120 cm,
    vivid colors that are NEVER blue or yellow.

Every actor has a generic label OBJ_0, OBJ_1, ... Each round you
get BOTH a screenshot AND a symbolic state list:

```json
[
  {{"label": "OBJ_0", "position": [x, y, z], "scale_cm": [sx, sy, sz],
   "rgb": [r, g, b], "mesh": "cube"|"sphere"}},
  ...
]
```

Identify the CUBE and PLATFORM by combining color AND size/shape:
  - PLATFORM = the entry with the largest XY footprint AND yellow-ish
    rgb. Visually it's the conspicuous flat slab in the screenshot.
  - CUBE = the entry with `mesh == "cube"` AND nearly-equal scale
    components AND blue-ish rgb. Visually it's the small blue cube
    sitting off to the side of the platform.
  - Distractor colors are always distinct from cube/platform, so
    color usually disambiguates; fall back to size/shape if needed.

The screenshot lets you double-check identity; the symbolic list
gives exact numbers for placement math.

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{{"cmd": "probe",   "label": "OBJ_2"}}                   // optional visual probe
{{"cmd": "move_to", "label": "OBJ_2", "to": [x, y, z]}}  // teleport to (x,y,z) cm; velocity zeroed
{{"cmd": "stop"}}                                         // end episode; physics settles, verifier runs
```

Compute the platform's top-center from its scale + position, and
`move_to` the cube there with z = platform_top + cube.side/2.

PASS CONDITION: cube's final XYZ within tolerance of platform top
(XY inside platform footprint, Z = platform_top + cube.side/2 ± 5
cm).

You have {max_rounds} rounds.
"""


async def _combined_observation(engine, params, prev_shot: Optional[Path]):
    out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    state = parse_state(out)
    state = annotate_with_colors(state, params)
    parts: list = []
    image_refs: list[str] = []
    if prev_shot and prev_shot.exists():
        parts.append({"type": "image_url",
                      "image_url": {"url": png_to_base64_url(prev_shot)}})
        image_refs.append(prev_shot.name)
    parts.append({"type": "text",
                   "text": "Current symbolic state:\n```json\n"
                           + json.dumps(state, indent=2)
                           + "\n```"})
    return parts, image_refs, {"channel": "combined", "state": state}


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id="combined",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_combined_observation,
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult"]
