"""object_ground — harness_single_iterate_offcenter.

Off-centre variant of harness_single_iterate. Random-view sampler
deliberately aims the camera at a point 50-90 cm OFFSET from the
target so the optical-axis hit (= initial single ball) is guaranteed
to land OFF the target. Forces real visual localisation instead of
"ball is gift-wrapped on the target, don't move it" (which the
on-target sampler in single_iterate / grid_iterate fell into).

Action format every round:
```json
{
  "dx": <cm>,  "dy": <cm>,  "dz": <cm>,
  "stop": false       // optional; true terminates the episode
}
```

Verifier: distance from the final ball to ``target_pose`` (or to the
extent line segment if ``extent_endpoints`` is configured) <=
``tolerance_cm``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common.api_receipt import save_api_receipt
from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    OPEN_APARTMENT_CODE,
    QUERY_MARKER_COLORS,
    camera_axis_raycast_code,
    extract_thought,
    get_object_pose_code,
    load_aliases,
    parse_camera_axis_hit,
    parse_pose_line,
    reset_pawn_code,
    resolve_alias,
    resolve_camera_view,
    take_screenshot,
    unwrap,
)
from .._bbox_markers import clear_bbox_markers_code as clear_query_markers_code
from .._bbox_markers_v2 import spawn_bbox_markers_code_v2 as spawn_anchored_markers_code

# Reuse the random-view helpers + screen-axis mapping from grid_iterate
# so the two harnesses are calibrated against the SAME camera math
# (random-view sampler / screen↔world axis mapping). If those move,
# both harnesses move together.
from ..harness_grid_iterate.task import (
    _random_view_facing,
    _screen_axis_mapping,
    _target_anchor,
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
    final_ball: Optional[dict]
    closest_ball_distance: Optional[float]
    tolerance_cm: float
    artifacts: dict = field(default_factory=dict)


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH.read_text(encoding="utf-8"))


_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def _extract_action(text: str) -> Optional[dict]:
    """Pull a JSON object containing dx/dy/dz."""
    candidates: list[str] = []
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1))
    for m in re.finditer(r"\{[\s\S]*?\"d[xyz]\"[\s\S]*?\}", text):
        candidates.append(m.group(0))
    for c in candidates:
        try:
            d = json.loads(c)
        except Exception:
            continue
        if isinstance(d, dict) and any(k in d for k in ("dx", "dy", "dz", "x", "y", "z")):
            return d
    return None


def _system_prompt(task_id: str, spec: dict, start_pose: dict,
                   target_alias: str, max_rounds: int,
                   anchor_world: dict) -> str:
    desc = spec.get("description", "")
    tol = spec.get("tolerance_cm", 25.0)

    loc = start_pose["location"]; rot = start_pose["control_rotation"]
    axis_map = _screen_axis_mapping(rot["Yaw"])

    def _verbose(axis: str) -> str:
        sign, ax = axis[0], axis[1]
        word = ax
        if sign == "+":
            return f"+{word} (i.e. INCREASE {word}: use d{word} > 0)"
        return f"-{word} (i.e. DECREASE {word}: use d{word} < 0)"

    cam_blurb = (
        f"Camera (pawn) world pose:\n"
        f"  location:  X={loc['X']:.1f}, Y={loc['Y']:.1f}, Z={loc['Z']:.1f}\n"
        f"  rotation:  Pitch={rot['Pitch']:.2f}, Yaw={rot['Yaw']:.2f}, "
        f"Roll={rot['Roll']:.2f}\n"
        f"  (this camera is fixed for the whole episode)\n\n"
        f"DIRECT SCREEN ↔ WORLD AXIS MAPPING (use these literally):\n"
        f"  - Move ball UP in screen     → {_verbose(axis_map['up'])}\n"
        f"  - Move ball DOWN in screen   → {_verbose(axis_map['down'])}\n"
        f"  - Move ball LEFT in screen   → {_verbose(axis_map['left'])}\n"
        f"  - Move ball RIGHT in screen  → {_verbose(axis_map['right'])}\n"
        f"  - Move ball FORWARD (deeper, away from camera) → {_verbose(axis_map['forward'])}\n"
        f"  - Move ball BACKWARD (closer to camera)        → {_verbose(axis_map['backward'])}\n"
    )

    return (
        "You are a visual-grounding probe. The screenshot below was taken\n"
        "in a photoreal UE5 kitchen scene. Somewhere in the scene there\n"
        f"is a target object: **{target_alias}** — described as\n"
        f"'{desc}'. You are NOT told its world coordinates — only its\n"
        "name and description. Your job is to nudge a single placed\n"
        "marker ball as close as possible to the target through small\n"
        "iterative offsets.\n\n"
        "*Important kitchen-object disambiguation* (these are easy to\n"
        "confuse from a single screenshot):\n"
        "  - A SAUCEPAN (\"pan\") has ONE long side handle, a flat round\n"
        "    bottom, and is shallow and wide.\n"
        "  - A SAUCEPOT (\"pot\") has TWO small loop handles on either\n"
        "    side, a tall cylindrical body, and a lid.\n"
        "  - A WOODEN UTENSIL is solid wood: light yellow / tan with\n"
        "    visible wood grain.\n"
        "  - A BLACK UTENSIL is solid matte black plastic / silicone.\n"
        "  - A SPATULA has a flat wide blade with slots; a SPOON has a\n"
        "    concave oval scoop (closed bowl, no slots).\n\n"
        f"TASK: {task_id}\n"
        f"target_alias: {target_alias}\n"
        f"tolerance:    {tol} cm  (PASS = ball within {tol} cm of the\n"
        f"              target)\n\n"
        f"{cam_blurb}\n"
        f"You have at most {max_rounds} rounds.\n\n"
        "ROUND 1 SETUP — the harness has pre-spawned ONE marker at the\n"
        f"centre of the screen (camera optical-axis raycast hit):\n"
        f"  initial ball world coord: ({anchor_world['X']:.1f}, "
        f"{anchor_world['Y']:.1f}, {anchor_world['Z']:.1f})\n"
        "The marker is rendered as a 12-edge wireframe AXIS-BBOX\n"
        "(~16 cm cube) centred on the ball's world coord:\n"
        "  - 4 RED   edges run along world ±X (parallel to X axis)\n"
        "  - 4 GREEN edges run along world ±Y\n"
        "  - 4 BLUE  edges run along world ±Z\n"
        "  - the persistent idx (always 0 in this single-ball harness)\n"
        "    is rendered as text at the cube centre.\n"
        "  - small '+X' / '+Y' / '+Z' text labels sit just past the\n"
        "    corresponding +face of the cube to disambiguate sign:\n"
        "      RED   '+X' label → POSITIVE world X (`dx > 0` toward it)\n"
        "      GREEN '+Y' label → POSITIVE world Y (`dy > 0` toward it)\n"
        "      BLUE  '+Z' label → POSITIVE world Z (`dz > 0` toward it)\n"
        "Use the screen↔world mapping above (or the visible bbox face\n"
        "labels) to decide which sign of dx/dy/dz moves the ball toward\n"
        "the target.\n\n"
        "DEPTH (IMPORTANT — balls are RAYCAST, not floating in 3D):\n"
        "  When the ball is (re-)spawned, the harness fires a ray from\n"
        "  the CAMERA through the requested (x,y,z) and snaps the ball\n"
        "  onto the FIRST SOLID MESH the ray hits. So:\n"
        "    1. The ball's reported world (X,Y,Z) is the surface it\n"
        "       landed on, not the abstract point you asked for.\n"
        "    2. If your ball is GONE in the new screenshot (or visibly\n"
        "       clipping into a surface), it has almost certainly been\n"
        "       swallowed by a mesh — slid inside a cabinet, under a\n"
        "       counter lip, or behind a wall.\n"
        "       FIX: lift the ball by RAISING dz (e.g. +10..+20 cm) so\n"
        "       the ray now hits the surface ABOVE the occluder. Don't\n"
        "       keep nudging dx/dy on a ball you can't see — lift first,\n"
        "       then re-aim laterally.\n\n"
        "Action format every round:\n"
        "```json\n"
        "{\n"
        '  "dx": 5,    "dy": -3,    "dz": 0,\n'
        '  "stop": false             // optional, true ends the episode\n'
        "}\n"
        "```\n\n"
        "STRATEGY:\n"
        "  - SMALL incremental moves (5-15 cm at a time). The harness\n"
        "    adds your (dx, dy, dz) to the ball's CURRENT absolute\n"
        "    position; the next round shows you where it landed.\n"
        "  - If the ball overshoots the target in some axis, halve the\n"
        "    step in the next round.\n"
        "  - Once the ball appears to overlap the target, set\n"
        "    `\"stop\": true` to end early (or just keep dx=dy=dz=0).\n\n"
        "The verifier reads the FINAL ball position and checks distance\n"
        "to the actual target's world coord.\n\n"
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
    max_rounds: int = 8,
    random_view: bool = False,
    view_seed: Optional[int] = None,
) -> EpisodeResult:
    gt = load_groundtruth()
    if task_id not in gt["tasks"]:
        raise ValueError(f"unknown task: {task_id!r}; choices: {list(gt['tasks'])}")
    spec = dict(gt["tasks"][task_id])
    aliases = load_aliases()
    target_alias = spec["target_alias"]
    target_leaf = resolve_alias(target_alias, aliases)
    tolerance = float(spec.get("tolerance_cm", 25.0))
    start_pose = resolve_camera_view(spec, "default", gt["scene_start_pose"])

    run_id = f"{vlm.model}_{task_id}_singleoff_bboxv2_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "task": task_id,
        "ablation": "harness_single_iterate", "info": "V",
        "action": "BallOffset (single ball, no grid scaffold)",
        "target_alias": target_alias, "tolerance_cm": tolerance,
        "max_rounds": max_rounds, "spec": spec,
        "random_view": random_view,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "task": task_id,
                       "max_rounds": max_rounds, "random_view": random_view},
        extra_config=cfg,
    )

    out = unwrap(await engine.python_exec(OPEN_APARTMENT_CODE))
    log.info("open level: %s", out.strip())
    if "LEVEL_LOADING" in out:
        await asyncio.sleep(12.0)
    else:
        await asyncio.sleep(1.0)

    target_pose = parse_pose_line(unwrap(
        await engine.python_exec(get_object_pose_code(target_leaf))))
    log.info("target alias=%s leaf=%s pose=%s",
             target_alias, target_leaf, target_pose)
    if target_pose is None:
        raise RuntimeError(f"target object not found: {target_alias!r} "
                           f"(mesh leaf {target_leaf!r})")

    if random_view:
        anchor_aim = _target_anchor(spec, target_pose)
        vs = view_seed if view_seed is not None else (
            (seed * 1009 + zlib.crc32(task_id.encode())) & 0x7FFFFFFF)
        # Off-centre aim: camera looks at a point 20-50 cm random
        # offset from the target so the central optical-axis hit
        # is OFF the target. MUST match harness_grid_iterate_offcenter's
        # aim_offset (20, 50) to produce byte-identical camera poses
        # for the grid_n=1 vs grid_n=3 ablation.
        start_pose = _random_view_facing(
            anchor_aim, vs, spec=spec,
            aim_offset_range_cm=(20.0, 50.0))
        log.info("random view (off-centre) seed=%d anchor=%s pose=%s",
                 vs, anchor_aim, start_pose)

    out = unwrap(await engine.python_exec(reset_pawn_code(start_pose)))
    log.info("reset pawn: %s", out.strip())
    await asyncio.sleep(0.7)

    unwrap(await engine.python_exec(clear_query_markers_code()))

    # Round-0: raycast camera optical-axis to get the anchor world
    # point. Spawn ONE marker there. No 9-ball grid scaffold.
    axis_out = unwrap(await engine.python_exec(camera_axis_raycast_code()))
    (workspace / "axis_raycast.txt").write_text(axis_out, encoding="utf-8")
    axis_hit = parse_camera_axis_hit(axis_out)
    if axis_hit is None:
        raise RuntimeError("camera optical-axis raycast did not hit anything")
    log.info("axis hit: %s", axis_hit)

    ball = {"idx": 0, "color": "red",
            "x": float(axis_hit["X"]),
            "y": float(axis_hit["Y"]),
            "z": float(axis_hit["Z"])}
    unwrap(await engine.python_exec(spawn_anchored_markers_code(
        [{"i": 0, "x": ball["x"], "y": ball["y"], "z": ball["z"],
          "color": "red"}])))
    await asyncio.sleep(0.3)

    SYSTEM = _system_prompt(task_id, spec, start_pose, target_alias,
                            max_rounds, axis_hit)

    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None

    for round_idx in range(1, max_rounds + 1):
        shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)

        parts: list = []
        if shot and shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(shot)}})
        text = (f"**Round {round_idx}/{max_rounds}**\n\n"
                f"Current marker ball world coord: ({ball['x']:.1f}, "
                f"{ball['y']:.1f}, {ball['z']:.1f})\n\n"
                "Reply with one JSON code fence containing the offset to\n"
                "apply to the ball this round (`dx`, `dy`, `dz`); add\n"
                "`\"stop\": true` if you're confident the ball is on the\n"
                "target.")
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, [shot.name] if shot else []),
            encoding="utf-8")

        VLM_ROUND_TIMEOUT_S = max(40.0, float(getattr(vlm, "timeout", 35.0)) + 5.0)
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, vlm.chat, messages),
                timeout=VLM_ROUND_TIMEOUT_S,
            )
        except asyncio.TimeoutError as api_error:
            save_api_receipt(vlm, messages, workspace, round_idx, error=api_error)
            raise RuntimeError("VLM API call failed; episode remains unscored") from api_error
        except Exception as api_error:
            save_api_receipt(vlm, messages, workspace, round_idx, error=api_error)
            raise RuntimeError("VLM API call failed; episode remains unscored") from api_error
        save_api_receipt(vlm, messages, workspace, round_idx)
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
                            "ball": dict(ball)})
            continue

        # Resolve next ball position.
        if "x" in action and "y" in action and "z" in action:
            ball["x"] = float(action["x"])
            ball["y"] = float(action["y"])
            ball["z"] = float(action["z"])
        else:
            ball["x"] += float(action.get("dx", 0))
            ball["y"] += float(action.get("dy", 0))
            ball["z"] += float(action.get("dz", 0))

        unwrap(await engine.python_exec(clear_query_markers_code()))
        unwrap(await engine.python_exec(spawn_anchored_markers_code(
            [{"i": 0, "x": ball["x"], "y": ball["y"], "z": ball["z"],
              "color": ball["color"]}])))

        history.append({"round": round_idx, "thought": thought,
                        "action": action, "ball": dict(ball)})

        # Early stop check (same logic as grid_iterate).
        bp = (ball["x"], ball["y"], ball["z"])
        tp = (target_pose["X"], target_pose["Y"], target_pose["Z"])

        def _point_dist(p, q):
            return math.sqrt(sum((a-b)**2 for a, b in zip(p, q)))

        def _segment_dist(p, a, b):
            ax, ay, az = a; bx, by, bz = b
            abx, aby, abz = bx-ax, by-ay, bz-az
            ab2 = abx*abx + aby*aby + abz*abz
            if ab2 == 0:
                return _point_dist(p, a)
            apx, apy, apz = p[0]-ax, p[1]-ay, p[2]-az
            t = max(0.0, min(1.0, (apx*abx + apy*aby + apz*abz) / ab2))
            cx = ax + abx * t; cy = ay + aby * t; cz = az + abz * t
            return _point_dist(p, (cx, cy, cz))

        eps = spec.get("extent_endpoints") or []
        if len(eps) >= 2:
            a = (eps[0]["X"], eps[0]["Y"], eps[0]["Z"])
            b = (eps[1]["X"], eps[1]["Y"], eps[1]["Z"])
            cur_dist = _segment_dist(bp, a, b)
        else:
            cur_dist = _point_dist(bp, tp)

        if action.get("stop") is True:
            log.info("round %d: agent stopped (claimed convergence, "
                     "dist=%.2f)", round_idx, cur_dist)
            early_stopped = True
            early_stop_reason = "agent_stop"
            try:
                await take_screenshot(
                    engine, f"round_{round_idx:03d}_final", workspace)
            except Exception:
                pass
            break

        if cur_dist <= tolerance:
            log.info("round %d: ball within tolerance (%.2f <= %.2f) "
                     "— early stop", round_idx, cur_dist, tolerance)
            early_stopped = True
            early_stop_reason = "harness_tolerance"
            try:
                await take_screenshot(
                    engine, f"round_{round_idx:03d}_final", workspace)
            except Exception:
                pass
            break

    # Final verifier — same logic but using the final ball position.
    bp = (ball["x"], ball["y"], ball["z"])
    tp = (target_pose["X"], target_pose["Y"], target_pose["Z"])
    pivot_dist = math.sqrt(sum((a-b)**2 for a, b in zip(bp, tp)))
    seg_dist: Optional[float] = None
    eps = spec.get("extent_endpoints") or []
    if len(eps) >= 2:
        a = (eps[0]["X"], eps[0]["Y"], eps[0]["Z"])
        b = (eps[1]["X"], eps[1]["Y"], eps[1]["Z"])
        abx, aby, abz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
        ab2 = abx*abx + aby*aby + abz*abz
        if ab2 == 0:
            seg_dist = pivot_dist
        else:
            apx, apy, apz = bp[0]-a[0], bp[1]-a[1], bp[2]-a[2]
            t = max(0.0, min(1.0,
                (apx*abx + apy*aby + apz*abz) / ab2))
            cx = a[0]+abx*t; cy = a[1]+aby*t; cz = a[2]+abz*t
            seg_dist = math.sqrt((bp[0]-cx)**2+(bp[1]-cy)**2+(bp[2]-cz)**2)
    final_dist = seg_dist if seg_dist is not None else pivot_dist
    passed = final_dist <= tolerance
    result = "PASS" if passed else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "task": task_id,
        "target_alias": target_alias, "target_pose": target_pose,
        "tolerance_cm": tolerance,
        "extent_endpoints": spec.get("extent_endpoints"),
        "result": result, "rounds": len(history),
        "early_stopped": early_stopped, "early_stop_reason": early_stop_reason,
        "anchor_axis_hit": axis_hit,
        "final_ball": ball,
        "closest_ball_distance_cm": round(final_dist, 2),
        "closest_ball_pivot_distance_cm": round(pivot_dist, 2),
        "closest_ball_segment_distance_cm": (round(seg_dist, 2)
                                             if seg_dist is not None else None),
        "history": history,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s — final_dist=%.2f tolerance=%.1f rounds=%d",
             result, final_dist, tolerance, len(history))

    try:
        unwrap(await engine.python_exec(clear_query_markers_code()))
    except Exception:
        pass

    return EpisodeResult(
        run_id=run_id, model=vlm.model, task=task_id, result=result,
        rounds=len(history),
        target_alias=target_alias, target_pose=target_pose,
        final_ball=ball,
        closest_ball_distance=round(final_dist, 2),
        tolerance_cm=tolerance,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "load_groundtruth"]
