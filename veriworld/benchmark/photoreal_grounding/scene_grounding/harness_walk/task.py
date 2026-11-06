"""scene_ground.harness_freeform — view-recovery navigation harness.

Per episode:
  1. OpenLevel(level_id) + lift pawn + warmup
  2. Sample target_pose from view_seed (deterministic per (level, seed))
  3. Teleport pawn to target_pose, take target screenshot
  4. Teleport pawn to spawn_pose, take initial screenshot
  5. For round 1..max_rounds:
       - Build prompt: target screenshot + current screenshot + last
         action's pose delta (collision-aware feedback)
       - VLM emits one walk / set_view / stop command
       - Apply via submit_walk_cmd, take new screenshot
  6. PASS if final pose within tolerance_xy_cm + tolerance_yaw_deg of target

Action format (one JSON code fence per round):
  ```json
  {"cmd": "walk",     "distance": 200}
  {"cmd": "set_view", "yaw": 90, "pitch": -5}
  {"cmd": "stop"}
  ```
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

from veriworld.common.api_receipt import save_api_receipt
from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from .._common import (
    GET_POSE_CODE,
    WALK_TICK_CODE,
    extract_action,
    extract_thought,
    load_groundtruth,
    open_level_and_warmup,
    parse_pose,
    sample_walkable_target_and_spawn,
    submit_walk_cmd,
    take_screenshot,
    teleport_pawn_code,
    unwrap,
    verify_pose_match,
    view_seed_for,
)

log = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    level: str
    result: str
    rounds: int
    target_pose: dict
    final_pose: Optional[dict]
    verdict: dict
    artifacts: dict = field(default_factory=dict)


SYSTEM_PROMPT_TEMPLATE = """\
You are a navigation agent inside a 3D photoreal scene. You are given a
TARGET screenshot taken from a fixed camera pose somewhere in the scene.
Your job is to walk the scene (with collision) and recover that view —
match both your XY position (within {tol_xy:.0f} cm) and your facing
direction (within {tol_yaw:.0f} degrees of the target yaw).

Each round you receive:
  - the TARGET screenshot (fixed for the whole episode)
  - your current first-person screenshot
  - your current world pose (X, Y, Z, yaw, pitch) and the previous
    action's outcome (distance actually moved — < requested means you
    hit a wall or obstacle)

ACTION FORMAT — emit exactly ONE JSON code fence per round:

```json
{{"cmd": "walk",     "distance": 200}}      // step forward N cm in current yaw direction (collision-aware)
{{"cmd": "set_view", "yaw": 90, "pitch": -5}} // rotate camera to absolute yaw / pitch (degrees)
{{"cmd": "stop"}}                          // claim convergence — ends episode
```

STRATEGY:
  - Most rounds should be `walk` actions — recovering a view almost always
    requires PHYSICAL MOVEMENT, not just rotation. Typical walks are
    200-400 cm. Just turning in place will not move you closer to the target.
  - Use `set_view` only to align your facing direction (1) at the start to
    point roughly toward the target landmarks before walking, and (2) at
    the end for fine yaw alignment once you're already close. Do NOT spend
    multiple rounds purely rotating in place.
  - `walk` distance_moved < requested cm means you collided with geometry;
    rotate and try a different direction.
  - You have at most {max_rounds} rounds total — typical successful runs
    will use ~1 set_view + 5-10 walks + maybe a final set_view + stop.
  - Emit {{"cmd": "stop"}} ONLY when your current view materially matches the
    target view (similar landmarks, similar framing). Do NOT stop just
    because the round budget is running out.

You will see your current pose (X, Y, Z, yaw) each round so you can
keep mental track of position. The target pose is hidden — you must
recover it from the visual match alone.
"""


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    level: str = "living_room",
    max_rounds: Optional[int] = None,
    tolerance_xy_cm: Optional[float] = None,
    tolerance_yaw_deg: Optional[float] = None,
    view_seed: Optional[int] = None,
) -> EpisodeResult:
    gt = load_groundtruth()
    if level not in gt["levels"]:
        raise ValueError(f"unknown level: {level!r}; choices: {list(gt['levels'])}")
    level_meta = gt["levels"][level]
    if max_rounds is None:
        max_rounds = int(gt.get("max_rounds", 15))
    tol_xy = float(tolerance_xy_cm if tolerance_xy_cm is not None
                   else gt.get("tolerance_xy_cm", 200.0))
    tol_yaw = float(tolerance_yaw_deg if tolerance_yaw_deg is not None
                    else gt.get("tolerance_yaw_deg", 15.0))

    vs = view_seed if view_seed is not None else view_seed_for(level, seed)
    run_id = (f"{vlm.model}_{level}_seed{seed}_walk_r{max_rounds}_"
              f"tol{int(tol_xy)}cm{int(tol_yaw)}deg")
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "level": level, "view_seed": vs,
        "max_rounds": max_rounds,
        "tolerance_xy_cm": tol_xy, "tolerance_yaw_deg": tol_yaw,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "level": level,
                       "max_rounds": max_rounds, "tolerance_xy_cm": tol_xy,
                       "tolerance_yaw_deg": tol_yaw},
        extra_config=cfg,
    )

    # 1. Open + warmup
    await open_level_and_warmup(engine, level_meta)

    # 2. Start walk_tick (needed BEFORE sampling target because the
    # sampler probes walkability via walk_tick).
    tick_response = unwrap(await engine.python_exec(WALK_TICK_CODE))
    if "[walk_tick] tick task launched" not in tick_response:
        raise RuntimeError(f"Scene movement initialization failed: {tick_response}")
    await asyncio.sleep(0.5)

    # 3. Sample a walkable target + walk-derived spawn. Resamples target
    # if walks fail (e.g., target inside furniture). Same view_seed →
    # same outcome across both harnesses for clean apples-to-apples.
    target_pose, spawn, attempts = await sample_walkable_target_and_spawn(
        engine, level_meta, vs)
    log.info("target_pose %s seed=%d view_seed=%d (after %d attempts) -> %s",
             level, seed, vs, attempts, target_pose)
    log.info("spawn (walk-derived): %s",
             {k: round(v, 1) for k, v in spawn.items()})

    # 4. Teleport to target, take target screenshot.
    out = unwrap(await engine.python_exec(teleport_pawn_code(
        target_pose["X"], target_pose["Y"], target_pose["Z"],
        target_pose["Yaw"], target_pose.get("Pitch", 0.0))))
    if parse_pose(out) is None:
        raise RuntimeError(f"failed to teleport to target: {out!r}")
    target_shot = await take_screenshot(engine, "00_target", workspace)
    if not target_shot or not target_shot.exists():
        raise RuntimeError("failed to capture target screenshot")
    log.info("target screenshot saved: %s", target_shot.name)

    # 5. Snap pawn to canonical spawn pose for the episode.
    out = unwrap(await engine.python_exec(teleport_pawn_code(
        spawn["X"], spawn["Y"], spawn["Z"],
        spawn["Yaw"], spawn.get("Pitch", 0.0))))
    initial_pose = parse_pose(out)
    log.info("spawned at: %s", initial_pose)

    # 5. Episode loop
    SYSTEM = SYSTEM_PROMPT_TEMPLATE.format(
        tol_xy=tol_xy, tol_yaw=tol_yaw, max_rounds=max_rounds)
    history: list = []
    last_log: Optional[dict] = None
    cur_pose: dict = dict(initial_pose) if initial_pose else dict(spawn)
    early_stopped = False
    early_stop_reason: Optional[str] = None
    final_pose = cur_pose

    for round_idx in range(1, max_rounds + 1):
        # Take current screenshot
        cur_shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)
        # Refresh pose from UE (in case walk_tick moved us)
        out = unwrap(await engine.python_exec(GET_POSE_CODE))
        live_pose = parse_pose(out)
        if live_pose:
            cur_pose = live_pose

        # Build prompt
        parts: list = []
        if target_shot and target_shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(target_shot)}})
        if cur_shot and cur_shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(cur_shot)}})

        text = (f"**Round {round_idx}/{max_rounds}**\n\n"
                f"Image 1 = TARGET view (fixed). Image 2 = your current view.\n\n"
                f"**Current pose**: X={cur_pose['X']:.0f}, Y={cur_pose['Y']:.0f}, "
                f"Z={cur_pose['Z']:.0f}, yaw={cur_pose['Yaw']:.1f}°, "
                f"pitch={cur_pose.get('Pitch',0):.1f}°\n\n")
        if last_log:
            text += (f"**Last action**: {last_log.get('cmd','?')}; "
                     f"moved {last_log.get('dist_moved','?')} cm; "
                     f"blocked={last_log.get('blocked', False)}\n\n")
        text += "Emit one JSON action (walk / set_view / stop)."
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        image_refs = []
        if target_shot: image_refs.append(target_shot.name)
        if cur_shot: image_refs.append(cur_shot.name)
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

        # VLM call
        VLM_TIMEOUT_S = float(getattr(vlm, "timeout", 180.0)) + 5.0
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages),
                timeout=VLM_TIMEOUT_S,
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
        action = extract_action(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(
            thought, encoding="utf-8")

        if not action or "cmd" not in action:
            log.warning("round %d: no valid action JSON", round_idx)
            history.append({"round": round_idx, "action": None,
                            "thought": thought, "parse_error": True,
                            "pose": cur_pose})
            # Don't crash — just skip this round; agent retries next.
            continue

        # Stop?
        if action.get("cmd") == "stop" or action.get("stop") is True:
            log.info("round %d: agent claimed convergence", round_idx)
            history.append({"round": round_idx, "action": action,
                            "thought": thought, "pose": cur_pose,
                            "stopped": True})
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

        # Enforce the advertised command surface: only walk / set_view reach walk_tick.
        if action.get("cmd") not in ("walk", "set_view"):
            log.warning("round %d: unsupported cmd %r (allowed: walk, set_view, stop)",
                        round_idx, action.get("cmd"))
            history.append({"round": round_idx, "action": action,
                            "thought": thought, "pose": cur_pose,
                            "parse_error": True})
            continue

        # Apply via walk_tick
        walk_log = await submit_walk_cmd(engine, action)
        last_log = walk_log
        (workspace / f"round_{round_idx:03d}_action.json").write_text(
            json.dumps(action, indent=2), encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_walklog.json").write_text(
            json.dumps(walk_log or {}, indent=2), encoding="utf-8")
        if walk_log is None:
            raise RuntimeError(f"Scene action transport timed out in round {round_idx}")

        # Refresh pose after walk
        out = unwrap(await engine.python_exec(GET_POSE_CODE))
        post_pose = parse_pose(out)
        if post_pose:
            cur_pose = post_pose
            final_pose = post_pose
        history.append({"round": round_idx, "action": action,
                        "thought": thought, "pose": cur_pose,
                        "walk_log": walk_log})

        # Auto early-stop: are we already within tolerance?
        verdict_now = verify_pose_match(target_pose, cur_pose, tol_xy, tol_yaw)
        if verdict_now["passed"]:
            log.info("round %d: pose within tolerance — early stop "
                     "(xy=%.0f, dyaw=%.1f)",
                     round_idx, verdict_now["xy_distance_cm"],
                     verdict_now["yaw_delta_deg"])
            early_stopped = True
            early_stop_reason = "harness_tolerance"
            break

    # 6. Final verify
    out = unwrap(await engine.python_exec(GET_POSE_CODE))
    fp = parse_pose(out) or final_pose
    verdict = verify_pose_match(target_pose, fp, tol_xy, tol_yaw)
    result = "PASS" if verdict["passed"] else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "level": level, "view_seed": vs,
        "max_rounds": max_rounds,
        "tolerance_xy_cm": tol_xy, "tolerance_yaw_deg": tol_yaw,
        "target_pose": target_pose,
        "spawn_pose": spawn,
        "initial_pose": initial_pose,
        "final_pose": fp,
        "result": result,
        "verdict": verdict,
        "rounds": len(history),
        "early_stopped": early_stopped,
        "early_stop_reason": early_stop_reason,
        "history": history,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s — %s (xy=%s cm, dyaw=%s°, rounds=%d)",
             run_id, result, verdict.get("xy_distance_cm"),
             verdict.get("yaw_delta_deg"), len(history))

    return EpisodeResult(
        run_id=run_id, model=vlm.model, level=level, result=result,
        rounds=len(history), target_pose=target_pose, final_pose=fp,
        verdict=verdict, artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
