"""scene_ground.harness_teleport — view-recovery via collision-aware
relative-step actions (variable distance per round).

Despite the legacy "teleport" directory name, this harness no longer
exposes absolute set_pose. The agent picks the step length each round
based on the visual feedback — same pattern as
``harness_win_tunnel`` in the VeriWorld benchmark.

Per round:
  - Show target screenshot + current screenshot + current pose
    + last action's outcome (distance moved, blocked flag).
  - Agent emits one ``forward`` / ``backward`` / ``turn`` / ``look`` /
    ``stop`` action.
  - Apply via ``step_forward_code`` / ``turn_yaw_code`` /
    ``look_pitch_code`` — every translation is sweep=True, so a wall
    in the path stops the move and the agent sees the actual reached
    pose, never a wall-cross.
  - Verifier checks pose tolerance after each apply (early-stop on hit).

Action format:

  ```json
  {"cmd": "forward",  "distance": 200}        // walk fwd N cm (collision-aware)
  {"cmd": "backward", "distance": 100}        // walk back N cm (collision-aware)
  {"cmd": "turn",     "degrees": 45}          // delta yaw (+CW / -CCW)
  {"cmd": "look",     "pitch": -10}           // absolute camera pitch
  {"cmd": "stop"}                             // claim convergence
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
    look_pitch_code,
    open_level_and_warmup,
    parse_moved,
    parse_pose,
    sample_walkable_target_and_spawn,
    step_forward_code,
    take_screenshot,
    teleport_pawn_code,
    turn_yaw_code,
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
TARGET screenshot taken from a fixed camera pose. Your job is to walk
the scene (with collision) and recover that view — match XY position
within {tol_xy:.0f} cm and yaw within {tol_yaw:.0f} degrees of the
target.

Each round you receive:
  - the TARGET screenshot (fixed for the whole episode)
  - your current first-person screenshot
  - your current world pose (X, Y, Z, yaw, pitch)
  - the previous action's outcome (distance actually moved; blocked flag
    set when a wall stopped you short)

ACTION FORMAT — emit exactly ONE JSON code fence per round:

```json
{{"cmd": "forward",  "distance": 200}}    // step fwd N cm along current yaw (collision-aware)
{{"cmd": "backward", "distance": 100}}    // step back N cm (collision-aware)
{{"cmd": "turn",     "degrees": 45}}      // rotate yaw by N degrees (positive = right / clockwise)
{{"cmd": "look",     "pitch": -10}}       // set absolute camera pitch (negative = look down)
{{"cmd": "stop"}}                          // claim convergence — ends the episode
```

CHOOSE THE DISTANCE / DEGREES YOURSELF every round — do not hard-code a
fixed step. Big strides (e.g. 300-500 cm forward, 60-90° turns) early
when you're far; smaller corrections (50-150 cm, 5-20°) once close.
After every action you see the actual cm moved.

DIAGNOSE THE GAP — translate vs rotate. Each round, classify the
mismatch between your current view and target:

  (a) **Same scene, different framing** (same furniture / walls
      visible, but at different distance or off-center) →
      **TRANSLATE** with `forward` / `backward`. DO NOT turn —
      turning will rotate the framing away from the match.

  (b) **Different scene entirely** (totally different walls /
      objects visible) → **TURN** to face the right direction
      first, then translate.

WHEN TO USE BACKWARD vs FORWARD:
  - **`backward` is critical and underused.** Once your view shows
    the SAME landmarks as target (correct facing), you may still be
    standing past the spot where the target camera was. `backward`
    retreats along your current facing without re-orienting —
    perfect for "the framing is right but I'm too close to the wall
    in front of me; the target shot was taken further back".
  - If you turned 180° to face target landmarks and your view now
    matches target's framing, the target POSITION is BEHIND you in
    world coordinates — use `backward`, not `forward`.
  - Don't keep turning when forward and backward already describe
    the axis you need to translate along. Most failed runs come
    from over-rotating after the framing is already correct.

  - Use `look` only for fine pitch alignment near the end.
  - You have at most {max_rounds} rounds total. Set "stop" ONLY when
    the current view materially matches the target (similar landmarks,
    similar framing). Do NOT stop just because the round budget is
    running out.
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
    tolerance_pitch_deg: Optional[float] = None,
    view_seed: Optional[int] = None,
) -> EpisodeResult:
    gt = load_groundtruth()
    if level not in gt["levels"]:
        raise ValueError(f"unknown level: {level!r}; choices: {list(gt['levels'])}")
    level_meta = gt["levels"][level]
    if max_rounds is None:
        max_rounds = int(gt.get("max_rounds", 15))
    tol_xy = float(tolerance_xy_cm if tolerance_xy_cm is not None
                   else gt.get("tolerance_xy_cm", 100.0))
    tol_yaw = float(tolerance_yaw_deg if tolerance_yaw_deg is not None
                    else gt.get("tolerance_yaw_deg", 10.0))
    tol_pitch = float(tolerance_pitch_deg if tolerance_pitch_deg is not None
                      else gt.get("tolerance_pitch_deg", 10.0))

    vs = view_seed if view_seed is not None else view_seed_for(level, seed)
    run_id = (f"{vlm.model}_{level}_seed{seed}_teleport_r{max_rounds}_"
              f"tol{int(tol_xy)}cm{int(tol_yaw)}y{int(tol_pitch)}p")
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "level": level, "view_seed": vs,
        "max_rounds": max_rounds,
        "tolerance_xy_cm": tol_xy, "tolerance_yaw_deg": tol_yaw,
        "tolerance_pitch_deg": tol_pitch,
    }
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "level": level,
                       "max_rounds": max_rounds, "tolerance_xy_cm": tol_xy,
                       "tolerance_yaw_deg": tol_yaw,
                       "tolerance_pitch_deg": tol_pitch},
        extra_config=cfg,
    )

    # 1. Open + warmup
    await open_level_and_warmup(engine, level_meta)

    # 2. Start walk_tick (the anchor sampler doesn't need it, but keep
    # it loaded for parity with harness_walk and for the no-anchor
    # fallback path inside sample_walkable_target_and_spawn).
    await engine.python_exec(WALK_TICK_CODE)
    await asyncio.sleep(0.5)

    # 3. Sample a walkable target + walk-derived spawn. Same view_seed
    # → same target/spawn as harness_walk for clean apples-to-apples.
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

    # 5. Snap pawn to canonical spawn pose for the episode + capture
    # the spawn-pose screenshot as ``00_initial.png``. The model sees
    # this image as its "current view" in round 1; thereafter each
    # ``round_NNN.png`` is the POST-action screenshot of round N (so
    # round_NNN.png is what round N+1's prompt shows). One image per
    # state, no duplicates.
    out = unwrap(await engine.python_exec(teleport_pawn_code(
        spawn["X"], spawn["Y"], spawn["Z"],
        spawn["Yaw"], spawn.get("Pitch", 0.0))))
    initial_pose = parse_pose(out)
    log.info("spawned at: %s", initial_pose)
    init_shot = await take_screenshot(engine, "00_initial", workspace)
    if not init_shot or not init_shot.exists():
        raise RuntimeError("failed to capture initial spawn screenshot")
    log.info("initial screenshot saved: %s", init_shot.name)

    # 6. Episode loop
    SYSTEM = SYSTEM_PROMPT_TEMPLATE.format(
        tol_xy=tol_xy, tol_yaw=tol_yaw, max_rounds=max_rounds)
    history: list = []
    last_log: Optional[dict] = None
    cur_pose: dict = dict(initial_pose) if initial_pose else dict(spawn)
    early_stopped = False
    early_stop_reason: Optional[str] = None
    final_pose = cur_pose
    prev_shot: Optional[Path] = init_shot  # what round N's prompt shows

    for round_idx in range(1, max_rounds + 1):
        out = unwrap(await engine.python_exec(GET_POSE_CODE))
        live_pose = parse_pose(out)
        if live_pose:
            cur_pose = live_pose

        parts: list = []
        if target_shot and target_shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(target_shot)}})
        if prev_shot and prev_shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(prev_shot)}})

        text = (f"**Round {round_idx}/{max_rounds}**\n\n"
                f"Image 1 = TARGET view (fixed). Image 2 = your current view.\n\n"
                f"**Current pose**: X={cur_pose['X']:.0f}, Y={cur_pose['Y']:.0f}, "
                f"Z={cur_pose['Z']:.0f}, yaw={cur_pose['Yaw']:.1f}°, "
                f"pitch={cur_pose.get('Pitch',0):.1f}°\n\n")
        if last_log:
            text += (f"**Last action**: {last_log.get('cmd','?')}; "
                     f"moved {last_log.get('dist_moved','?')} cm; "
                     f"blocked={last_log.get('blocked', False)}\n\n")
        text += "Emit one JSON action (forward / backward / turn / look / stop)."
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        image_refs = []
        if target_shot: image_refs.append(target_shot.name)
        if prev_shot: image_refs.append(prev_shot.name)
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

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
            continue

        cmd = action.get("cmd")

        if cmd == "stop" or action.get("stop") is True:
            log.info("round %d: agent claimed convergence", round_idx)
            history.append({"round": round_idx, "action": action,
                            "thought": thought, "pose": cur_pose,
                            "stopped": True})
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

        # Dispatch relative actions. Each branch produces a `code` to
        # python_exec and a small action_log dict for the next prompt.
        code: Optional[str] = None
        action_log: dict = {"cmd": cmd}
        try:
            if cmd == "forward":
                dist = float(action.get("distance", 0.0))
                code = step_forward_code(max(0.0, dist), sign=1.0)
                action_log["requested"] = dist
            elif cmd == "backward":
                dist = float(action.get("distance", 0.0))
                code = step_forward_code(max(0.0, dist), sign=-1.0)
                action_log["requested"] = dist
            elif cmd == "turn":
                deg = float(action.get("degrees", action.get("delta", 0.0)))
                code = turn_yaw_code(deg)
                action_log["requested"] = deg
            elif cmd == "look":
                pitch = float(action.get("pitch", cur_pose.get("Pitch", 0.0)))
                code = look_pitch_code(pitch)
                action_log["requested"] = pitch
            else:
                log.warning("round %d: unknown cmd %r", round_idx, cmd)
                history.append({"round": round_idx, "action": action,
                                "thought": thought, "parse_error": True,
                                "pose": cur_pose})
                continue
        except (KeyError, ValueError, TypeError) as e:
            log.warning("round %d: malformed %s action: %s", round_idx, cmd, e)
            history.append({"round": round_idx, "action": action,
                            "thought": thought, "parse_error": True,
                            "pose": cur_pose})
            continue

        out = unwrap(await engine.python_exec(code))
        moved = parse_moved(out)
        if moved is None:
            log.warning("round %d: parse_moved failed for %s: %r",
                        round_idx, cmd, out)
            new_pose = cur_pose
            blocked = False
            dist_moved = 0.0
        else:
            new_pose = {"X": moved["X"], "Y": moved["Y"], "Z": moved["Z"],
                        "Yaw": moved["Yaw"], "Pitch": moved["Pitch"]}
            blocked = (cmd in ("forward", "backward")) and (not moved["reached"])
            dist_moved = moved.get("dist_moved", 0.0)
            if blocked:
                log.info("round %d: %s blocked by collision; moved %.0f cm",
                         round_idx, cmd, dist_moved)
        action_log["dist_moved"] = round(dist_moved, 1)
        action_log["blocked"] = blocked
        last_log = action_log

        cur_pose = new_pose
        final_pose = new_pose
        (workspace / f"round_{round_idx:03d}_action.json").write_text(
            json.dumps(action, indent=2), encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_actionlog.json").write_text(
            json.dumps(action_log, indent=2), encoding="utf-8")

        # POST-action screenshot. ``round_NNN.png`` reflects the world
        # state AFTER round N's action, so when this round triggers
        # early-stop the saved image actually shows what the agent
        # achieved (not the pre-action spawn frame).
        post_shot = await take_screenshot(
            engine, f"round_{round_idx:03d}", workspace)
        if post_shot and post_shot.exists():
            prev_shot = post_shot

        history.append({"round": round_idx, "action": action,
                        "thought": thought, "pose": new_pose,
                        "action_log": action_log,
                        "screenshot": post_shot.name if post_shot else None})

        verdict_now = verify_pose_match(
            target_pose, new_pose, tol_xy, tol_yaw, tol_pitch)
        if verdict_now["passed"]:
            log.info("round %d: pose within tolerance — early stop "
                     "(xy=%.0f, dyaw=%.1f, dpitch=%.1f)",
                     round_idx, verdict_now["xy_distance_cm"],
                     verdict_now["yaw_delta_deg"],
                     verdict_now["pitch_delta_deg"])
            early_stopped = True
            early_stop_reason = "harness_tolerance"
            break

    # 7. Final verify
    out = unwrap(await engine.python_exec(GET_POSE_CODE))
    fp = parse_pose(out) or final_pose
    verdict = verify_pose_match(target_pose, fp, tol_xy, tol_yaw, tol_pitch)
    result = "PASS" if verdict["passed"] else "FAIL"

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "level": level, "view_seed": vs,
        "max_rounds": max_rounds,
        "tolerance_xy_cm": tol_xy, "tolerance_yaw_deg": tol_yaw,
        "tolerance_pitch_deg": tol_pitch,
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
    log.info("%s — %s (xy=%s cm, dyaw=%s°, dpitch=%s°, rounds=%d)",
             run_id, result, verdict.get("xy_distance_cm"),
             verdict.get("yaw_delta_deg"),
             verdict.get("pitch_delta_deg"), len(history))

    return EpisodeResult(
        run_id=run_id, model=vlm.model, level=level, result=result,
        rounds=len(history), target_pose=target_pose, final_pose=fp,
        verdict=verdict, artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
