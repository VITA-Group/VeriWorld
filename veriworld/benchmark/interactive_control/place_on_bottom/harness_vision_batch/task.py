"""Batched placement control with the corresponding single-arm observations.

The retained configuration uses three rounds, six total actions, and up
to five actions per batch. See the release README for the single-arm budget.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from veriworld.common import png_to_base64_url

from .._episode import EpisodeResult
from .._episode_batch import run_episode_batch
from .._shared import QUERY_STATE_CODE, parse_state, unwrap

SYSTEM_PROMPT = """\
You are an agent in a 3D physics scene. There is exactly:

  * one PLATFORM - a LARGE FLAT BOX. Footprint roughly 200-350 cm on
    each side, height 50-150 cm. Color is yellow-ish (rgb â‰ˆ 1.0, 0.9,
    0.1). It is the widest, flattest object in the scene.
  * one CUBE - a small physics-enabled cube (equal sides 70-110 cm).
    Color is blue-ish (rgb â‰ˆ 0.1, 0.3, 1.0). It starts on the ground
    off to the side of the platform.
  * 2-8 DISTRACTOR objects - random CUBE OR SPHERE primitives,
    side/diameter 70-120 cm, vivid colors (red, green, magenta, cyan,
    orange, purple, pink, chartreuse, etc.). NONE of them are blue or
    yellow.

Each round you receive:
  * a SCREENSHOT of the current scene
  * an ANONYMOUS state list of every OBJ_i with its world position
    and scale_cm. The list does NOT include color or mesh type - you
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

ACTION FORMAT - emit ONE JSON code fence per round containing an
ORDERED BATCH of 1-{batch_cap} actions, executed strictly in order:

```json
{{"actions": [
  {{"cmd": "probe",   "label": "OBJ_2"}},
  {{"cmd": "move_to", "label": "OBJ_2", "to": [x, y, z]}},
  {{"cmd": "stop"}}
]}}
```

  * probe   - lifts the object by 200 cm so the round's closing
              screenshot reveals which physical box it is; the
              harness restores it after the screenshot
  * move_to - teleport the object to absolute world coords (cm);
              zeros velocity
  * stop    - end episode; physics settles, verifier runs; any
              actions after stop in the batch are ignored

You have {max_rounds} rounds and a TOTAL budget of
{max_total_actions} actions across all rounds. Feedback (new
screenshot + state) arrives only AFTER a whole batch executes, so
plan the batch carefully. After moving the cube ON TOP of the
platform, include a stop action.

PASS CONDITION: cube's final XYZ within tolerance of platform top
(XY inside platform footprint, Z = platform.top + cube.side/2 Â± 5
cm).
"""


def _strip_to_anonymous(state: list) -> list:
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


async def run(engine, seed, vlm, logger, *, max_rounds: int = 3,
              max_total_actions: int = 6, batch_cap: int = 5) -> EpisodeResult:
    return await run_episode_batch(
        engine, seed, vlm, logger,
        harness_id="vision_batch",
        system_prompt=SYSTEM_PROMPT,
        observation_fn=_vision_observation,
        package=__package__,
        max_rounds=max_rounds,
        max_total_actions=max_total_actions,
        batch_cap=batch_cap,
    )


__all__ = ["run", "EpisodeResult"]
