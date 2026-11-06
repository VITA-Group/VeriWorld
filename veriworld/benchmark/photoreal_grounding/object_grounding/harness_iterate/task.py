"""object_ground — harness_iterate.

Multi-round visual-grounding probe. The agent is told the camera pose
and a single target object's NAME (e.g., "wooden spoon"). The agent
is NOT told the target's world coord. The agent must:

  1. Drop one or more colored balls at world coords it picks.
  2. Look at the resulting screenshot — each ball lands somewhere in
     the scene; the target object is also visible.
  3. Update the ball positions (full replacement each round) so at
     least one ball moves closer to the target.
  4. Each round, also declare which color is closest to the target.
  5. After max_rounds, the verifier looks up the target alias's
     actual world pose and checks distance(claimed-closest-ball,
     target) <= tolerance.

What this measures: the model's ability to (a) visually localize a
named object in a screenshot, (b) reason about world-frame ball
placement using only the camera pose as anchor, and (c) iteratively
refine ball positions based on visual feedback. No movement of scene
objects, no rotation, no candidate-list disambiguation — just the
single grounding loop.

PASS = distance(claimed-closest-ball-at-final-round, target_alias) <= tolerance_cm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    OPEN_APARTMENT_CODE,
    QUERY_MARKER_COLORS,
    camera_axis_raycast_code,
    clear_query_markers_code,
    extract_thought,
    get_object_pose_code,
    load_aliases,
    parse_camera_axis_hit,
    parse_pose_line,
    reset_pawn_code,
    resolve_alias,
    resolve_camera_view,
    spawn_query_markers_code,
    take_screenshot,
    unwrap,
)

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
GROUNDTRUTH = HERE.parent / "groundtruth.json"


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    task: str
    result: str
    rounds: int
    target_alias: str
    target_pose: Optional[dict]
    final_balls: list
    claimed_closest_color: Optional[str]
    closest_ball_distance: Optional[float]
    tolerance_cm: float
    artifacts: dict = field(default_factory=dict)


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH.read_text(encoding="utf-8"))


_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def _extract_action(text: str) -> Optional[dict]:
    """Pull a `{"balls": [...], "closest_color": "..."}` JSON object."""
    candidates: list[str] = []
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1))
    for m in re.finditer(r"\{[\s\S]*?\"balls\"[\s\S]*?\}\s*\}", text):
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = json.loads(c)
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("balls"), list):
            return d
    return None


def _system_prompt(task_id: str, spec: dict, start_pose: dict,
                   target_alias: str, max_rounds: int,
                   axis_hit: Optional[dict]) -> str:
    desc = spec.get("description", "")
    tol = spec.get("tolerance_cm", 10.0)
    max_balls = int(spec.get("max_balls", 4))
    available = spec.get("available_colors") or list(QUERY_MARKER_COLORS.keys())
    avail_str = ", ".join(f'"{c}"' for c in available)

    loc = start_pose["location"]; rot = start_pose["control_rotation"]
    cam_blurb = (
        f"Camera (pawn) world pose:\n"
        f"  location:  X={loc['X']:.1f}, Y={loc['Y']:.1f}, Z={loc['Z']:.1f}\n"
        f"  rotation:  Pitch={rot['Pitch']:.2f}, Yaw={rot['Yaw']:.2f}, "
        f"Roll={rot['Roll']:.2f}\n"
        f"  (this camera is fixed for the whole episode)\n"
    )
    if axis_hit:
        cam_blurb += (
            f"\n"
            f"Camera optical-axis raycast (pre-computed by the harness): the\n"
            f"ray cast forward from the camera through the center of the\n"
            f"viewport hits world point ({axis_hit['X']:.1f}, "
            f"{axis_hit['Y']:.1f}, {axis_hit['Z']:.1f}).\n"
            f"This is the world coord at the CENTER of your screenshot. The\n"
            f"target object is somewhere in the visible region around this\n"
            f"point — usually within ~150 cm if the camera is set up to look\n"
            f"at a kitchen surface. Use this as your anchor when picking ball\n"
            f"world coords; balls placed too far from this point will fall\n"
            f"outside the camera's field of view and be invisible to you.\n"
        )

    return (
        "You are a visual-grounding probe. The screenshot below was taken\n"
        "in a photoreal UE5 kitchen scene. Somewhere in the scene there is\n"
        f"a target object: **{target_alias}** (described informally as\n"
        f"'{desc}'). You are NOT told the target's world coordinates — only\n"
        "its name. Your job is to drop colored balls in the world such that\n"
        "at least one of them lands as close as possible to the target.\n\n"
        f"TASK: {task_id}\n"
        f"target_alias: {target_alias}\n"
        f"tolerance:    {tol} cm  (PASS = your claimed-closest ball is within\n"
        f"              {tol} cm of the actual target)\n\n"
        f"{cam_blurb}\n"
        f"You have {max_rounds} rounds. Each round:\n"
        "  - the harness shows you the current screenshot (with whatever\n"
        "    balls you placed last round, if any),\n"
        "  - you reply with a JSON object containing the FULL set of balls\n"
        "    you want in the scene NEXT (full replacement — colors not in\n"
        "    your reply get despawned), AND a `closest_color` declaring\n"
        "    which color is closest to the target.\n\n"
        f"Action format (max {max_balls} balls; each color must be unique):\n"
        "```json\n"
        "{\n"
        '  "balls": [\n'
        '    {"color": "red",  "x": <cm>, "y": <cm>, "z": <cm>},\n'
        '    {"color": "blue", "x": <cm>, "y": <cm>, "z": <cm>}\n'
        "  ],\n"
        '  "closest_color": "red"\n'
        "}\n"
        "```\n\n"
        f"Available colors: {avail_str}\n\n"
        "STRATEGY:\n"
        "  Round 1: drop a few balls at world coords scattered around the\n"
        "    region you think the target is in (use the camera pose + the\n"
        "    visible scene as your anchor — e.g., if the camera looks +Y at\n"
        "    pitch -30 deg from Z=200, the surface in front of it is roughly\n"
        "    Z=80-90 cm; pick X,Y within ~50 cm of where the named object\n"
        "    appears in the image).\n"
        "  Round 2+: look at where each colored ball landed in the\n"
        "    screenshot. Adjust positions (in world coords) so the ball\n"
        "    closest to the target moves directly toward it. You can also\n"
        "    leave some balls fixed as visual reference points and only\n"
        "    move the candidate-closest ball.\n\n"
        "The verifier reads ONLY the `closest_color` from your final round\n"
        "and checks the distance from THAT ball's actual world coord to the\n"
        "target's actual world coord.\n\n"
        "RESPONSE FORMAT each round:\n"
        "  Brief THOUGHT (plain text, optional), then ONE JSON code fence.\n"
    )


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    task_id: str = "find_wooden_spoon_pot_view",
    max_rounds: int = 6,
) -> EpisodeResult:
    gt = load_groundtruth()
    if task_id not in gt["tasks"]:
        raise ValueError(f"unknown task: {task_id!r}; choices: {list(gt['tasks'])}")
    spec = gt["tasks"][task_id]
    aliases = load_aliases()
    start_pose = resolve_camera_view(spec, "default", gt["scene_start_pose"])
    target_alias = spec["target_alias"]
    target_leaf = resolve_alias(target_alias, aliases)
    tolerance = float(spec.get("tolerance_cm", 10.0))

    run_id = f"{vlm.model}_{task_id}_iterate_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": "harness_iterate", "info": "V",
        "action": "BallPlacement+ClosestColor",
        "target_alias": target_alias, "tolerance_cm": tolerance,
        "max_rounds": max_rounds, "spec": spec,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "task": task_id,
                       "max_rounds": max_rounds},
        extra_config=cfg,
    )

    out = unwrap(await engine.python_exec(OPEN_APARTMENT_CODE))
    log.info("open level: %s", out.strip())
    if "LEVEL_LOADING" in out:
        await asyncio.sleep(12.0)
    else:
        await asyncio.sleep(1.0)

    out = unwrap(await engine.python_exec(reset_pawn_code(start_pose)))
    log.info("reset pawn: %s", out.strip())
    await asyncio.sleep(0.7)

    # Cleanup any leftover markers.
    unwrap(await engine.python_exec(clear_query_markers_code()))

    # Camera optical-axis raycast — the world coord at the center of the
    # viewport. Given to the agent as a concrete FOV anchor so balls
    # don't get placed outside the screenshot.
    axis_hit = parse_camera_axis_hit(unwrap(
        await engine.python_exec(camera_axis_raycast_code(start_pose))))
    log.info("camera axis hit: %s", axis_hit)

    # Look up the target's actual world pose for verification — NOT shown
    # to the agent.
    target_pose = parse_pose_line(unwrap(
        await engine.python_exec(get_object_pose_code(target_leaf))))
    log.info("target alias=%s leaf=%s pose=%s",
             target_alias, target_leaf, target_pose)
    if target_pose is None:
        raise RuntimeError(f"target object not found: {target_alias!r} "
                           f"(mesh leaf {target_leaf!r})")

    SYSTEM = _system_prompt(task_id, spec, start_pose, target_alias,
                            max_rounds, axis_hit)
    history: list = []
    current_balls: list = []  # [{color, x, y, z}, ...]
    claimed_closest: Optional[str] = None

    for round_idx in range(1, max_rounds + 1):
        shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)

        parts: list = []
        if shot and shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(shot)}})
        text = f"**Round {round_idx}/{max_rounds}**\n\n"
        if current_balls:
            text += "Current balls in the scene (from last round):\n"
            for b in current_balls:
                text += (f"  {b['color']:<8s} -> ({b['x']:.1f}, {b['y']:.1f}, "
                         f"{b['z']:.1f})\n")
            text += "\n"
            if claimed_closest:
                text += f"You previously claimed `{claimed_closest}` was closest.\n\n"
        else:
            text += ("No balls in the scene yet. Place your first batch by\n"
                     "emitting `balls` in the JSON.\n\n")
        text += ("Reply with one JSON code fence containing `balls` "
                 "(full replacement) and `closest_color`.")
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, [shot.name] if shot else []),
            encoding="utf-8")

        t0 = time.time()
        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            log.error("VLM error at round %d: %s", round_idx, e)
            break
        log.info("round %d: VLM %.1fs", round_idx, time.time() - t0)
        (workspace / f"round_{round_idx:03d}_response.txt").write_text(
            response, encoding="utf-8")
        thought = extract_thought(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(
            thought, encoding="utf-8")

        action = _extract_action(response)
        if not action:
            log.warning("round %d: no valid action JSON", round_idx)
            history.append({"round": round_idx, "thought": thought,
                            "action": None, "parse_error": True,
                            "current_balls": list(current_balls),
                            "claimed_closest": claimed_closest})
            continue

        new_balls_raw = action.get("balls") or []
        new_balls: list = []
        seen_colors = set()
        for b in new_balls_raw:
            if not isinstance(b, dict):
                continue
            try:
                color = str(b["color"]).lower()
                if color in seen_colors:
                    log.warning("round %d: duplicate color %s, skipping", round_idx, color)
                    continue
                seen_colors.add(color)
                new_balls.append({
                    "color": color,
                    "x": float(b["x"]), "y": float(b["y"]), "z": float(b["z"]),
                })
            except (KeyError, TypeError, ValueError) as e:
                log.warning("round %d: malformed ball %s: %s", round_idx, b, e)
                continue

        # Despawn old, spawn new.
        unwrap(await engine.python_exec(clear_query_markers_code()))
        if new_balls:
            spawn_queries = [
                {"x": b["x"], "y": b["y"], "z": b["z"], "color": b["color"]}
                for b in new_balls
            ]
            unwrap(await engine.python_exec(spawn_query_markers_code(spawn_queries)))
        log.info("round %d: replaced balls (count=%d)", round_idx, len(new_balls))

        current_balls = new_balls
        new_closest = action.get("closest_color")
        if isinstance(new_closest, str) and new_closest.lower() in seen_colors:
            claimed_closest = new_closest.lower()
        elif isinstance(new_closest, str):
            log.warning("round %d: closest_color %r not in current colors %s",
                        round_idx, new_closest, list(seen_colors))
            # keep prior claim if any
        history.append({"round": round_idx, "thought": thought,
                        "action": action, "current_balls": list(current_balls),
                        "claimed_closest": claimed_closest})

    # Verify: distance from claimed-closest ball to target_pose.
    closest_distance: Optional[float] = None
    closest_ball: Optional[dict] = None
    if claimed_closest:
        for b in current_balls:
            if b["color"] == claimed_closest:
                closest_ball = b
                break
    if closest_ball:
        closest_distance = math.sqrt(
            (closest_ball["x"] - target_pose["X"]) ** 2 +
            (closest_ball["y"] - target_pose["Y"]) ** 2 +
            (closest_ball["z"] - target_pose["Z"]) ** 2)

    passed = (closest_distance is not None and closest_distance <= tolerance)
    result = "PASS" if passed else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "task": task_id,
        "target_alias": target_alias, "target_pose": target_pose,
        "tolerance_cm": tolerance,
        "camera_axis_hit": axis_hit,
        "result": result, "rounds": len(history),
        "final_balls": current_balls,
        "claimed_closest_color": claimed_closest,
        "closest_ball_world_coord": closest_ball,
        "closest_ball_distance_cm": (round(closest_distance, 2)
                                     if closest_distance is not None else None),
        "history": history,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s — claimed=%s dist=%s tol=%s",
             result, claimed_closest, closest_distance, tolerance)

    # Cleanup so a subsequent attach run starts blank.
    try:
        unwrap(await engine.python_exec(clear_query_markers_code()))
    except Exception:
        pass

    return EpisodeResult(
        run_id=run_id, model=vlm.model, task=task_id, result=result,
        rounds=len(history),
        target_alias=target_alias, target_pose=target_pose,
        final_balls=current_balls,
        claimed_closest_color=claimed_closest,
        closest_ball_distance=(round(closest_distance, 2)
                               if closest_distance is not None else None),
        tolerance_cm=tolerance,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "load_groundtruth"]
