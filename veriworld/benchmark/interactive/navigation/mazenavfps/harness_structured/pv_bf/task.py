"""MazeNavFPS — PV / Bf ablation (pure vision).

Conditions (hard-coded, no branching):

* **info = PV** — agent sees a labelled history grid of the last N
  screenshots plus the current view. NO coordinates, NO raycast, NO
  position log. Feedback is binary BLOCKED/ok.
* **action = Bf** — batch of free commands per turn.
* **materials = 3**, **grid_size = 4**, **max_steps = 30**,
  **history_size = 6** — defaults.
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
    build_setup_code,
    extract_moves_batch,
    extract_thought,
    make_history_grid,
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
    "You are a navigation agent inside a 3D maze.\n"
    "You must explore and find the goal.\n\n"
    "Each turn you see a grid of your recent screenshots plus the current view.\n"
    "NO coordinates. NO raycast. Only BLOCKED/ok feedback per move.\n"
    "Navigate visually — track your own position mentally.\n\n"
    "RESPONSE FORMAT:\n"
    "THOUGHT (plain text), then JSON:\n"
    'Send a BATCH of free movement commands each turn.\n'
    '```json\n{"moves": [{"cmd": "forward", "distance": 600}, {"cmd": "turn", "degrees": 90}]}\n```\n'
    '- {"cmd": "forward", "distance": D} — move D cm (stops at walls).\n'
    '- {"cmd": "turn", "degrees": A} — turn (positive = right).\n'
    'Send 3-6 commands per batch.\n'
    "IMPORTANT:\n"
    "- The history grid shows your recent screenshots labelled by step.\n"
    "- If blocked, you see 'BLOCKED' — no distance information.\n"
    "- Explore systematically. Avoid revisiting dead ends.\n"
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
    history_size: int = 6,
    initial_yaw: Optional[float] = None,
) -> EpisodeResult:
    from ...generate_params import generate as generate_maze
    params = generate_maze(seed=seed, grid_size=grid_size)

    cell_size = params["cell_size"]
    start_grid = params["start_grid"]
    goal_grid = params["goal_grid"]
    yaw_val = float(initial_yaw) if initial_yaw is not None else params["initial_yaw"]

    run_id = (
        f"{vlm.model}_{grid_size}x{grid_size}_seed{seed}_PV_Bf_mat{materials}_"
        f"yaw{int(yaw_val)}_s{max_steps}"
    )
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "pv_bf", "info": "PV", "action": "Bf",
        "grid_size": grid_size, "materials": materials,
        "initial_yaw": yaw_val, "max_steps": max_steps, "history_size": history_size,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__,
        model_name=run_id,
        seed=seed,
        resolved_args={
            "seed": seed, "model": vlm.model,
            "grid_size": grid_size, "materials": materials,
            "max_steps": max_steps, "initial_yaw": yaw_val,
            "history_size": history_size,
        },
        extra_config=cfg,
    )

    if "LEVEL_SWITCHING" in unwrap(await engine.python_exec(LEVEL_SWITCH_CODE)):
        await asyncio.sleep(5)
    await engine.python_exec(CLEAN_VOXEL_CODE)
    await engine.python_exec(build_setup_code(params, materials))
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

    screenshot_history: list[Path] = [init_shot] if init_shot else []
    prev_log: list = []
    trajectory: list = []
    passed = False

    for step in range(1, max_steps + 1):
        shot = init_shot if step == 1 else await take_screenshot(
            engine, f"step_{step - 1:03d}", screenshot_dir, workspace
        )
        if shot:
            screenshot_history.append(shot)

        parts: list = []
        history_paths = screenshot_history[:-1][-history_size:]
        grid_url = make_history_grid(history_paths, step)
        if grid_url:
            parts.append({"type": "image_url", "image_url": {"url": grid_url}})
        if shot:
            parts.append({"type": "image_url", "image_url": {"url": png_to_base64_url(shot)}})

        text = f"**Step {step}/{max_steps}** ({max_steps - step} steps remaining)\n\n"
        if prev_log:
            text += "**Previous move results:**\n"
            for e in prev_log:
                if not isinstance(e, dict):
                    continue
                status = "BLOCKED" if e.get("blocked") else "ok"
                text += f"- {e['cmd']}: {status}\n"
            text += "\n"
        text += "Explore systematically. Avoid revisiting dead ends."
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": parts},
        ]

        image_refs: list[str] = []
        if grid_url:
            image_refs.append(f"<history grid: last {len(history_paths)} screenshots>")
        if shot:
            image_refs.append(shot.name)
        (workspace / f"step_{step:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

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
        "ablation": "pv_bf", "result": result,
        "steps": len(trajectory), "final_distance": round(final_dist, 1),
        "solution_length": len(params["solution_path"]),
    }
    (workspace / "episode_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("%s: %s in %d steps (dist=%.0f)", run_id, result, len(trajectory), final_dist)

    return EpisodeResult(
        run_id=run_id, model=vlm.model, result=result, steps=len(trajectory),
        final_distance=round(final_dist, 1),
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
