"""Tunnel — Aim-and-Fly ablation.

Conditions (hard-coded):

* **action = AF** — compound single action per turn:
  ``{"see": "...", "yaw": N, "pitch": M, "forward": D}``.
  The harness decomposes it server-side into separate
  ``turn`` / ``move_z`` / ``forward`` commands.
* **info = visual only** — screenshot every turn, no position log,
  no raycast. Purpose: force the model to reason about 3D geometry
  directly from the image.
* **cardinal = True** — irrelevant (no map shown) but kept for
  consistency with tunnel's movement log.

This is a fundamentally different **action space** from Bf, so it is
its own subfolder (per the ablation-isolation rule).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.common.screenshot import default_screenshot_dir
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    CLEAN_VOXEL_CODE,
    LEVEL_SWITCH_CODE,
    MOVE_CAMERA_CODE,
    build_setup_code,
    extract_moves_aim_and_fly,
    extract_thought,
    parse_navlog,
    take_screenshot,
    unwrap,
)

log = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    result: str
    steps: int
    final_distance: float
    artifacts: dict = field(default_factory=dict)


SYSTEM_PROMPT = (
    "You are a camera inside a 3D tunnel carved through colorful walls.\n"
    "Each turn you see a screenshot. You make ONE action per turn.\n"
    "You must follow the tunnel from the green marker to the red marker.\n\n"
    "## HOW TO READ THE SCREENSHOT\n"
    "- Center of image = straight ahead at your current pitch angle.\n"
    "- Left/right = horizontal direction.\n"
    "- Up/down in image = vertical direction (the tunnel goes up and down!).\n"
    "- Dark HOLE or OPENING = tunnel path to follow.\n\n"
    "## ACTION\n"
    'Reply with JSON:\n'
    '```json\n{"see": "hole upper-right", "yaw": 20, "pitch": -15, "forward": 150}\n```\n'
    "- **see** (REQUIRED): describe where the hole is / what colour walls you see.\n"
    "- **yaw**: degrees to turn horizontally (positive = right).\n"
    "- **pitch**: tilt camera (positive = look up).\n"
    "- **forward**: cm to fly in the aimed direction (negative = back up).\n\n"
    "## STRATEGY\n"
    "1. HOLE VISIBLE → aim (yaw + pitch) + forward 150 to fly through.\n"
    "2. NO HOLE → back up (forward=-150), or turn sideways, or look up/down.\n"
    "3. BLOCKED → forward=-150 to back up.\n\n"
    "## AVOID BACKTRACKING\n"
    "- Holes are in COLORED walls — remember the colour you just flew THROUGH.\n"
    "- Same-colour wall with a hole = the way back. Do NOT re-enter.\n"
)


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    tunnel_radius: float = 80.0,
    colorful: bool = True,
    max_steps: int = 50,
    initial_yaw: Optional[float] = None,
) -> EpisodeResult:
    from ...generate_params import generate as generate_tunnel
    params = generate_tunnel(seed=seed)
    cell_size = params["cell_size"]
    start_grid = params["start_grid"]
    goal_grid = params["goal_grid"]
    yaw_val = float(initial_yaw) if initial_yaw is not None else params.get("initial_yaw", 0)

    run_id = (
        f"{vlm.model}_AF_hole{int(tunnel_radius)}_"
        f"{'color' if colorful else 'mono'}_s{max_steps}"
    )
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "af", "action": "AF", "info": "V",
        "tunnel_radius": tunnel_radius, "colorful": colorful,
        "max_steps": max_steps, "initial_yaw": yaw_val,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__,
        model_name=run_id,
        seed=seed,
        resolved_args={
            "seed": seed, "model": vlm.model,
            "tunnel_radius": tunnel_radius, "colorful": colorful,
            "max_steps": max_steps, "initial_yaw": yaw_val,
        },
        extra_config=cfg,
    )

    if "LEVEL_SWITCHING" in unwrap(await engine.python_exec(LEVEL_SWITCH_CODE)):
        await asyncio.sleep(5)
    await engine.python_exec(CLEAN_VOXEL_CODE)
    await engine.python_exec(build_setup_code(params, tunnel_radius, colorful))
    await asyncio.sleep(2)
    await engine.python_exec(MOVE_CAMERA_CODE)
    await asyncio.sleep(0.5)

    screenshot_dir = default_screenshot_dir("demo1")
    init_shot = await take_screenshot(engine, "step_000", screenshot_dir, workspace)

    cur_x = (start_grid[1] + 0.5) * cell_size
    cur_y = (start_grid[0] + 0.5) * cell_size
    cur_yaw = yaw_val
    goal_x = (goal_grid[1] + 0.5) * cell_size
    goal_y = (goal_grid[0] + 0.5) * cell_size

    prev_log: list = []
    trajectory: list = []
    passed = False

    for step in range(1, max_steps + 1):
        shot = init_shot if step == 1 else await take_screenshot(
            engine, f"step_{step - 1:03d}", screenshot_dir, workspace
        )

        parts: list = []
        if shot:
            parts.append({"type": "image_url", "image_url": {"url": png_to_base64_url(shot)}})

        text = f"**Step {step}/{max_steps}** ({max_steps - step} steps remaining)\n\n"
        if prev_log:
            text += "**Previous move result:**\n"
            for e in prev_log:
                if not isinstance(e, dict):
                    continue
                status = "BLOCKED" if e.get("blocked") else "ok"
                text += f"- {e['cmd']}: {status}\n"
            text += "\n"
        text += "Aim at any dark hole. If no hole visible, back up or turn to find one."
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ]

        (workspace / f"step_{step:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, [shot.name] if shot else []),
            encoding="utf-8")

        t0 = time.time()
        try:
            response = await asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            log.error("VLM error at step %d: %s", step, e)
            break
        log.info("step %d: VLM %.1fs", step, time.time() - t0)

        (workspace / f"step_{step:03d}_response.txt").write_text(response, encoding="utf-8")
        thought = extract_thought(response)
        moves = extract_moves_aim_and_fly(response)
        (workspace / f"step_{step:03d}_thought.txt").write_text(thought, encoding="utf-8")

        await engine.python_exec(
            f"import builtins; builtins._NAV_MOVES = {json.dumps(moves)}; builtins._NAV_DONE = False"
        )
        for _ in range(60):
            await asyncio.sleep(0.5)
            if "True" in unwrap(await engine.python_exec(
                "import builtins; print(getattr(builtins, '_NAV_DONE', False))"
            )):
                break

        await asyncio.sleep(0.3)
        log_out = unwrap(await engine.python_exec(
            "import builtins, json\n"
            "log = getattr(builtins, '_NAV_LOG', [])\n"
            "if isinstance(log, dict): log = [log]\n"
            "print('NAVLOG:' + json.dumps(log))"
        ))
        log_entries = parse_navlog(log_out)
        (workspace / f"step_{step:03d}_movelog.txt").write_text(
            json.dumps(log_entries, indent=2), encoding="utf-8"
        )

        if log_entries:
            last = log_entries[-1]
            cur_x = float(last.get("to_x", last.get("x", cur_x)))
            cur_y = float(last.get("to_y", last.get("y", cur_y)))
            cur_yaw = float(last.get("yaw", cur_yaw))

        trajectory.append({"step": step, "moves": moves, "log": log_entries})
        prev_log = log_entries

        dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
        if dist <= cell_size * 2:
            passed = True
            break

    final_dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
    result = "PASS" if passed else "FAIL"
    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "af", "tunnel_radius": tunnel_radius, "colorful": colorful,
        "result": result, "steps": len(trajectory),
        "final_distance": round(final_dist, 1),
    }
    (workspace / "episode_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("%s: %s in %d steps (dist=%.0f)", run_id, result, len(trajectory), final_dist)

    return EpisodeResult(
        run_id=run_id, model=vlm.model, result=result, steps=len(trajectory),
        final_distance=round(final_dist, 1),
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
