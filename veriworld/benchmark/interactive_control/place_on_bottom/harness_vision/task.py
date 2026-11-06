"""place_on_platform — VISION harness.

Per round, agent observation = current screenshot + an ANONYMOUS
state list (label, position, scale_cm only — no rgb, no mesh, no
role hint). The agent has the positions and sizes needed to compute
placement math, but must use the screenshot to figure out which OBJ
is the cube vs platform vs distractor (since color and mesh-type
tags are stripped). Action set is probe / move_to / stop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from veriworld.common import png_to_base64_url

from .._episode import EpisodeResult, run_episode
from .._shared import QUERY_STATE_CODE, parse_state, unwrap

SYSTEM_PROMPT = """\
You are an agent in a 3D physics scene. There is exactly:

  * one PLATFORM — a LARGE FLAT BOX. Footprint roughly 200-350 cm on
    each side, height 50-150 cm. Color is yellow-ish (rgb ≈ 1.0, 0.9,
    0.1). It is the widest, flattest object in the scene.
  * one CUBE — a small physics-enabled cube (equal sides 70-110 cm).
    Color is blue-ish (rgb ≈ 0.1, 0.3, 1.0). It starts on the ground
    off to the side of the platform.
  * 2-8 DISTRACTOR objects — random CUBE OR SPHERE primitives,
    side/diameter 70-120 cm, vivid colors (red, green, magenta, cyan,
    orange, purple, pink, chartreuse, etc.). NONE of them are blue or
    yellow.

Each round you receive:
  * a SCREENSHOT of the current scene
  * an ANONYMOUS state list of every OBJ_i with its world position
    and scale_cm. The list does NOT include color or mesh type — you
    must figure out which OBJ_i is the cube / platform / each
    distractor by matching the visual scene to the position +
    scale_cm entries.

```json
[
  {{"label": "OBJ_0", "position": [x, y, z], "scale_cm": [sx, sy, sz]}},
  ...
]
```

Identify cube + platform by visual+geometric reasoning:
  - the PLATFORM is the entry whose `scale_cm` has the LARGEST XY
    product (and a moderate Z height) AND looks yellow + flat in the
    screenshot.
  - the CUBE is the entry whose `scale_cm` has all three components
    nearly equal AND looks blue + cube-shaped in the screenshot AND
    is sitting on the ground.
  - everything else is a distractor.

Use the position + scale_cm for exact placement math:
  - platform top is at `position.z + scale_cm[2] / 2`
  - cube target z = `platform_top + cube.scale_cm[2] / 2`

Then teleport the cube onto the platform so it settles within
tolerance.

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{{"cmd": "probe",   "label": "OBJ_2"}}                   // lifts OBJ_2 by 200cm so the next screenshot reveals which physical box it is; harness restores it after
{{"cmd": "move_to", "label": "OBJ_2", "to": [x, y, z]}}  // teleport OBJ_2 to absolute world coords (cm); zeros velocity
{{"cmd": "stop"}}                                         // end episode; physics settles, verifier runs
```

Probe sparingly (1-3 times). After moving the cube ON TOP of the
platform, emit `stop` to let physics settle.

PASS CONDITION: cube's final XYZ within tolerance of platform top
(XY inside platform footprint, Z = platform.top + cube.side/2 ± 5
cm).

You have {max_rounds} rounds.
"""


def _strip_to_anonymous(state: list) -> list:
    """Drop rgb / mesh / role hints — keep only label + position +
    scale_cm. The agent uses the screenshot to figure out which OBJ
    corresponds to which physical object."""
    return [
        {"label": s["label"], "position": s["position"], "scale_cm": s["scale_cm"]}
        for s in state
    ]


async def _vision_observation(engine, params, prev_shot: Optional[Path]):
    out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    state = _strip_to_anonymous(parse_state(out))
    parts: list = []
    image_refs: list[str] = []
    if prev_shot and prev_shot.exists():
        parts.append({"type": "image_url",
                      "image_url": {"url": png_to_base64_url(prev_shot)}})
        image_refs.append(prev_shot.name)
    parts.append({"type": "text",
                   "text": "Anonymous state (positions + scale_cm only):\n```json\n"
                           + json.dumps(state, indent=2)
                           + "\n```"})
    return parts, image_refs, {"channel": "vision", "state": state}


async def run(engine, seed, vlm, logger, *, max_rounds: int = 8) -> EpisodeResult:
    return await run_episode(
        engine, seed, vlm, logger,
        harness_id="vision",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_vision_observation,
        package=__package__,
        max_rounds=max_rounds,
    )


__all__ = ["run", "EpisodeResult"]
