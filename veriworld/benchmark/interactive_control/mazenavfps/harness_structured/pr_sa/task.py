"""MazeNavFPS — PR / Sa ablation.

Same PR info channel as ``pr_bf`` (raycast + position log, **no
screenshot**), but the action space is **single free command per turn**
instead of a batch of 3-6. Mirrors the ``vrp_bf`` -> ``vrp_sa`` action
change one harness layer up.

Conditions (hard-coded, no branching):

* **info = PR** — agent receives, each turn, a 4-direction raycast
  scan (Front/Right/Back/Left wall distances in cm) AND a running log
  of explored positions annotated with BLOCKED / OPEN per cardinal
  direction. **No screenshot.**
* **action = Sa** — exactly one ``{"cmd": ..., ...}`` command per turn.
* **cardinal = True**, **materials = 3**, **grid_size = 4**,
  **max_steps = 30** — defaults (matching ``pr_bf`` / ``vrp_sa``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    CLEAN_VOXEL_CODE,
    LEVEL_SWITCH_CODE,
    MOVE_CAMERA_CODE,
    RAYCAST_CODE,
    PositionTracker,
    build_setup_code,
    extract_moves_single_free,
    extract_thought,
    parse_navlog,
    parse_raycast,
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
    "You are a navigation agent inside a 3D maze. You must explore "
    "and find the goal using STRUCTURED FEEDBACK ONLY — there is no "
    "screenshot.\n\n"
    "Each turn you receive:\n"
    "  - a 4-direction raycast scan (Front/Right/Back/Left wall "
    "    distances in cm), measured from your current position and "
    "    relative to your current facing\n"
    "  - your current (x, y) position and facing\n"
    "  - a running log of every position you have visited, each "
    "    annotated with BLOCKED/OPEN per cardinal direction (N/E/S/W) "
    "    derived from your previous raycasts and move outcomes\n\n"
    "RESPONSE FORMAT:\n"
    "THOUGHT (plain text), then JSON:\n"
    'Send EXACTLY ONE movement command each turn.\n'
    '```json\n{"move": {"cmd": "forward", "distance": 600}}\n```\n'
    '- {"cmd": "forward", "distance": D} — move D cm (stops at walls).\n'
    '- {"cmd": "turn", "degrees": A} — turn (positive = right).\n'
    'One command per turn — not a batch. To both turn and then move, emit only the turn now; the forward comes next turn.\n'
    "IMPORTANT:\n"
    "- If blocked, the distance_moved tells you how far the wall was.\n"
    "- The raycast tells you wall distance in each cardinal direction "
    "from the current pose — use it to plan reachable distances.\n"
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
    from ...generate_params import generate as generate_maze
    params = generate_maze(seed=seed, grid_size=grid_size)

    cell_size = params["cell_size"]
    start_grid = params["start_grid"]
    goal_grid = params["goal_grid"]
    yaw_val = float(initial_yaw) if initial_yaw is not None else params["initial_yaw"]

    run_id = (
        f"{vlm.model}_{grid_size}x{grid_size}_seed{seed}_PR_Sa_mat{materials}_"
        f"yaw{int(yaw_val)}_s{max_steps}_card"
    )
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "pr_sa", "info": "PR", "action": "Sa",
        "grid_size": grid_size, "materials": materials,
        "initial_yaw": yaw_val, "max_steps": max_steps, "cardinal": True,
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
        },
        extra_config=cfg,
    )

    if "LEVEL_SWITCHING" in unwrap(await engine.python_exec(LEVEL_SWITCH_CODE)):
        await asyncio.sleep(5)
    unwrap(await engine.python_exec(CLEAN_VOXEL_CODE))
    unwrap(await engine.python_exec(build_setup_code(params, materials)))
    await asyncio.sleep(2)
    unwrap(await engine.python_exec(MOVE_CAMERA_CODE))
    await asyncio.sleep(0.5)

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
        # Raycast scan from current pose (before this step's move).
        raycast_text = parse_raycast(unwrap(await engine.python_exec(RAYCAST_CODE)))

        text = f"**Step {step}/{max_steps}** ({max_steps - step} steps remaining)\n\n"
        if prev_log:
            text += "**Previous move results:**\n"
            for e in prev_log:
                if not isinstance(e, dict):
                    continue
                status = "BLOCKED" if e.get("blocked") else "ok"
                text += (
                    f"- {e.get('cmd','?')}: pos=({e.get('to_x','?')}, {e.get('to_y','?')}) "
                    f"yaw={e.get('yaw','?')} {status}\n"
                )
            text += "\n"
        text += f"**Current position**: ({cur_x:.0f}, {cur_y:.0f})\n\n"
        text += f"**Raycast scan:**\n{raycast_text}\n\n"
        text += tracker.format_map(cur_x, cur_y, cur_yaw) + "\n\n"
        text += "Explore systematically. Avoid revisiting dead ends."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        (workspace / f"step_{step:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, []), encoding="utf-8")

        t0 = time.time()
        try:
            response = await asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            (workspace / f"step_{step:03d}_transport_error.json").write_text(
                json.dumps({"kind": "api_error", "error_type": type(e).__name__, "scored": False}), encoding="utf-8")
            raise RuntimeError("Navigation API request failed; episode is unscored") from e
        log.info("step %d: VLM %.1fs", step, time.time() - t0)

        (workspace / f"step_{step:03d}_response.txt").write_text(response, encoding="utf-8")
        raw_response = getattr(vlm, "last_response", None)
        if raw_response is not None:
            (workspace / f"step_{step:03d}_response.json").write_text(
                json.dumps(raw_response, ensure_ascii=False, default=str), encoding="utf-8")
        thought = extract_thought(response)
        moves = extract_moves_single_free(response) or [{"cmd": "forward", "distance": 200}]
        (workspace / f"step_{step:03d}_thought.txt").write_text(thought, encoding="utf-8")

        await engine.python_exec(
            f"import builtins; builtins._NAV_MOVES = {json.dumps(moves)}; builtins._NAV_DONE = False; builtins._NAV_LOG = []"
        )
        for _ in range(60):
            await asyncio.sleep(0.5)
            if "True" in unwrap(await engine.python_exec(
                "import builtins; print(getattr(builtins, '_NAV_DONE', False))"
            )):
                break
        else:
            (workspace / f"step_{step:03d}_transport_error.json").write_text(
                json.dumps({"kind": "movement_timeout", "scored": False}), encoding="utf-8")
            raise TimeoutError("Navigation controller did not finish the committed action")

        await asyncio.sleep(0.3)
        log_out = unwrap(await engine.python_exec(
            "import builtins, json\n"
            "log = getattr(builtins, '_NAV_LOG', [])\n"
            "if isinstance(log, dict): log = [log]\n"
            "print('NAVLOG:' + json.dumps(log))"
        ))
        log_entries = parse_navlog(log_out)
        (workspace / f"step_{step:03d}_movelog.txt").write_text(
            "Raycast (before move):\n" + raycast_text + "\n\n"
            + json.dumps(log_entries, indent=2),
            encoding="utf-8",
        )

        if log_entries:
            tracker.update(log_entries)
            last = log_entries[-1]
            cur_x = float(last.get("to_x", last.get("x", cur_x)))
            cur_y = float(last.get("to_y", last.get("y", cur_y)))
            cur_yaw = float(last.get("yaw", cur_yaw))

        trajectory.append({
            "step": step, "moves": moves, "log": log_entries,
            "raycast": raycast_text,
        })
        prev_log = log_entries

        dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
        if dist <= cell_size:
            passed = True
            break

    final_dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
    result = "PASS" if passed else "FAIL"
    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "ablation": "pr_sa", "result": result,
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
