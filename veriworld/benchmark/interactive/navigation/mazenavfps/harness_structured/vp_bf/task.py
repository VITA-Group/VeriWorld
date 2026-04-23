"""MazeNavFPS — VP / Bf ablation.

Conditions (hard-coded, no branching):

* **info = VP** — agent sees each turn's screenshot AND a running log of
  explored positions annotated with BLOCKED / OPEN per direction.
* **action = Bf** — agent sends a **batch** of **free** commands per
  turn: ``{"cmd": "forward", "distance": D}`` and
  ``{"cmd": "turn", "degrees": A}``.
* **cardinal = True** — position log uses N/E/S/W labels.
* **materials = 3**, **grid_size = 4**, **max_steps = 30** — defaults.

These are editable at the top of :func:`run`. To run a different
condition combination, copy this folder and change the constants.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.common.screenshot import default_screenshot_dir
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    CLEAN_VOXEL_CODE,
    LEVEL_SWITCH_CODE,
    MOVE_CAMERA_CODE,
    PositionTracker,
    build_setup_code,
    extract_moves_batch,
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
    positions_explored: int
    artifacts: dict = field(default_factory=dict)


SYSTEM_PROMPT = (
    "You are a navigation agent inside a 3D maze.\n"
    "You must explore and find the goal.\n\n"
    "Each turn you see a screenshot AND a list of explored positions with BLOCKED/OPEN info.\n"
    "Use both the screenshot and the coordinate log to navigate.\n\n"
    "RESPONSE FORMAT:\n"
    "THOUGHT (plain text), then JSON:\n"
    'Send a BATCH of free movement commands each turn.\n'
    '```json\n{"moves": [{"cmd": "forward", "distance": 600}, {"cmd": "turn", "degrees": 90}]}\n```\n'
    '- {"cmd": "forward", "distance": D} — move D cm (stops at walls).\n'
    '- {"cmd": "turn", "degrees": A} — turn (positive = right).\n'
    'Send 3-6 commands per batch.\n'
    "IMPORTANT:\n"
    "- If blocked, the distance_moved tells you how far the wall was.\n"
    "- Explore systematically. Avoid revisiting dead ends.\n"
    "- No goal coordinates given. Pure exploration.\n"
)


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    grid_size: int = 4,
    materials: int = 3,
    max_steps: int = 30,
    initial_yaw: Optional[float] = None,
) -> EpisodeResult:
    # Generate maze
    from ...generate_params import generate as generate_maze
    params = generate_maze(seed=seed, grid_size=grid_size)

    cell_size = params["cell_size"]
    start_grid = params["start_grid"]
    goal_grid = params["goal_grid"]
    yaw_val = float(initial_yaw) if initial_yaw is not None else params["initial_yaw"]

    run_id = (
        f"{vlm.model}_{grid_size}x{grid_size}_seed{seed}_VP_Bf_mat{materials}_"
        f"yaw{int(yaw_val)}_s{max_steps}_card"
    )
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "vp_bf", "info": "VP", "action": "Bf",
        "grid_size": grid_size, "materials": materials,
        "initial_yaw": yaw_val, "max_steps": max_steps, "cardinal": True,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")

    # Write model-level config.json + reproduce.bat/sh. ``snapshot_model``
    # overwrites config.json with a merge of the standard schema and
    # ``cfg`` above, so downstream tools that look for ``run_id`` /
    # ``ablation`` / ``info`` / ``action`` still find them.
    logger.snapshot_model(
        task_module=__package__,
        model_name=run_id,
        seed=seed,
        resolved_args={
            "seed": seed, "model": vlm.model,
            "grid_size": grid_size, "materials": materials,
            "max_steps": max_steps, "initial_yaw": yaw_val,
        },
        extra_config=cfg,
    )

    # Setup UE
    if "LEVEL_SWITCHING" in unwrap(await engine.python_exec(LEVEL_SWITCH_CODE)):
        await asyncio.sleep(5)
    await engine.python_exec(CLEAN_VOXEL_CODE)
    await engine.python_exec(build_setup_code(params, materials))
    await asyncio.sleep(2)
    await engine.python_exec(MOVE_CAMERA_CODE)
    await asyncio.sleep(0.5)

    screenshot_dir = default_screenshot_dir("demo1")
    init_shot = await take_screenshot(engine, "step_000", screenshot_dir, workspace)

    tracker = PositionTracker(use_cardinal=True)
    cur_x = (start_grid[1] + 0.5) * cell_size
    cur_y = (start_grid[0] + 0.5) * cell_size
    cur_yaw = yaw_val
    goal_x = (goal_grid[1] + 0.5) * cell_size
    goal_y = (goal_grid[0] + 0.5) * cell_size
    tracker.visit_order.append((round(cur_x), round(cur_y)))

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
            text += "**Previous move results:**\n"
            for e in prev_log:
                if not isinstance(e, dict):
                    continue
                status = "BLOCKED" if e.get("blocked") else "ok"
                text += (
                    f"- {e['cmd']}: pos=({e.get('to_x','?')}, {e.get('to_y','?')}) "
                    f"yaw={e.get('yaw','?')} {status}\n"
                )
            text += "\n"
        text += f"**Current position**: ({cur_x:.0f}, {cur_y:.0f})\n\n"
        text += tracker.format_map(cur_x, cur_y, cur_yaw) + "\n\n"
        text += "Explore systematically. Avoid revisiting dead ends."
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
        moves = extract_moves_batch(response) or [{"cmd": "forward", "distance": 200}]
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
            tracker.update(log_entries)
            last = log_entries[-1]
            cur_x = float(last.get("to_x", last.get("x", cur_x)))
            cur_y = float(last.get("to_y", last.get("y", cur_y)))
            cur_yaw = float(last.get("yaw", cur_yaw))

        trajectory.append({"step": step, "moves": moves, "log": log_entries})
        prev_log = log_entries

        dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
        if dist <= cell_size:
            passed = True
            break

    final_dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
    result = "PASS" if passed else "FAIL"
    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "vp_bf", "result": result,
        "steps": len(trajectory), "final_distance": round(final_dist, 1),
        "positions_explored": len(tracker.visit_order),
        "solution_length": len(params["solution_path"]),
    }
    (workspace / "episode_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("%s: %s in %d steps (dist=%.0f)", run_id, result, len(trajectory), final_dist)

    return EpisodeResult(
        run_id=run_id, model=vlm.model, result=result, steps=len(trajectory),
        final_distance=round(final_dist, 1),
        positions_explored=len(tracker.visit_order),
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
