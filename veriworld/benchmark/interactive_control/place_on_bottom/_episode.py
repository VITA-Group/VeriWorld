"""Shared episode-loop runner used by all three harnesses.

The three harnesses (vision / symbolic / combined) only differ in
**which observation channels go into each round's user message**:
screenshot only, symbolic state list only, or both. Everything else
— action dispatch, settle, verify, summary — is identical.

A harness's ``task.py`` calls :func:`run_episode` and supplies:

* ``harness_id``       — short tag baked into ``run_id`` and summary
* ``system_prompt``    — full system message
* ``observation_fn``   — async callable
                          ``(engine, params, post_shot) -> (parts, image_refs, meta)``
                          where ``parts`` is a list of OpenAI-style message
                          parts (image_url + text) for the round's user
                          message, ``image_refs`` lists screenshot
                          filenames for the prompt log, and ``meta`` is a
                          dict appended into the per-round history (e.g.
                          the symbolic state list captured that round).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt
from veriworld.infra.interactive.engine import InteractiveEngine

from ._response_diagnostics import vlm_call_info
from .generate_params import generate as generate_params
from ._shared import (
    extract_action, extract_thought,
    lift_code, move_to_code,
    open_level_and_setup,
    parse_pre_pos,
    probe_lift_code, probe_restore_code,
    settle_and_verify,
    take_screenshot,
    unwrap,
)

log = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    seed: int
    result: str
    rounds: int
    verdict: dict
    artifacts: dict = field(default_factory=dict)


def _summarize_action(action_log: dict) -> str:
    cmd = action_log.get("cmd", "?")
    if cmd == "probe":
        return f"probed {action_log.get('label')}"
    if cmd == "move_to":
        return f"moved {action_log.get('label')} to {action_log.get('to', [])}"
    if cmd == "stop":
        return "stopped"
    return cmd


async def _apply_action(engine, action: dict) -> dict:
    cmd = action.get("cmd")
    out = {"cmd": cmd}
    if cmd == "probe":
        label = str(action.get("label", "")).strip()
        if not label:
            return {**out, "error": "missing label"}
        # Lift via probe_lift_code which (a) prints the PRE-LIFT
        # position so we can restore exactly and (b) freezes physics
        # so gravity doesn't drag the cube down during the screenshot
        # delay (which used to leave the cube hundreds of cm below
        # ground after a buggy relative-restore).
        ue_out = unwrap(await engine.python_exec(probe_lift_code(label, 200.0)))
        pre = parse_pre_pos(ue_out)
        out["label"] = label
        out["lifted"] = 200.0
        if pre:
            out["pre_position"] = [round(pre[0], 2), round(pre[1], 2), round(pre[2], 2)]
    elif cmd == "move_to":
        label = str(action.get("label", "")).strip()
        to = action.get("to") or action.get("position")
        if not label or not (isinstance(to, list) and len(to) >= 3):
            return {**out, "error": "missing label or to=[x,y,z]"}
        x, y, z = float(to[0]), float(to[1]), float(to[2])
        await engine.python_exec(move_to_code(label, x, y, z))
        out["label"] = label
        out["to"] = [round(x, 1), round(y, 1), round(z, 1)]
    elif cmd == "stop":
        pass
    else:
        out["error"] = f"unknown cmd {cmd!r}"
    return out


async def _restore_probe(engine, label: str, pre_position=None) -> None:
    """Snap the probed actor back to its pre-lift position and
    re-enable physics simulation. If we don't have an absolute
    pre-position (parse failed), fall back to a relative -200 lift —
    less precise but better than leaving the actor floating."""
    if pre_position and len(pre_position) >= 3:
        x, y, z = float(pre_position[0]), float(pre_position[1]), float(pre_position[2])
        await engine.python_exec(probe_restore_code(label, x, y, z, re_enable_physics=True))
    else:
        await engine.python_exec(lift_code(label, -200.0))


async def run_episode(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    harness_id: str,
    system_prompt: str,
    observation_fn: Callable[[InteractiveEngine, dict, Optional[Path]], Awaitable[tuple]],
    package: str,
    max_rounds: int = 8,
) -> EpisodeResult:
    params = generate_params(seed)
    cube_label = "PLACE_OBJ_" + params["cube"]["obj_label"].split("_")[-1]
    plat_label = "PLACE_OBJ_" + params["platform"]["obj_label"].split("_")[-1]

    run_id = f"{vlm.model}_seed{seed}_{harness_id}_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": harness_id, "max_rounds": max_rounds,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=package, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "max_rounds": max_rounds},
        extra_config=cfg,
    )

    await open_level_and_setup(engine, params)
    await asyncio.sleep(0.5)

    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None

    init_shot = await take_screenshot(engine, "00_initial", workspace)
    prev_shot: Optional[Path] = init_shot

    SYSTEM = system_prompt.format(max_rounds=max_rounds)

    for round_idx in range(1, max_rounds + 1):
        last = history[-1].get("action_log") if history else None
        last_summary = _summarize_action(last) if last else None

        parts, image_refs, obs_meta = await observation_fn(engine, params, prev_shot)
        if harness_id in ("vision", "combined", "vision_batch") and not image_refs:
            raise RuntimeError("Required current image is missing; episode is unscored")
        if len(obs_meta.get("state", [])) != params["n_total_objects"]:
            raise RuntimeError("Incomplete runtime scene state; episode is unscored")
        # Append a tail message with last-action summary + reminder.
        tail = (f"**Round {round_idx}/{max_rounds}** "
                f"({max_rounds - round_idx} remaining)\n\n"
                f"Emit one JSON action (probe / move_to / stop). After moving the cube "
                f"onto the platform, stop so physics can settle.")
        if last_summary:
            tail += f"\n\nLast action: {last_summary}"
        parts.append({"type": "text", "text": tail})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

        VLM_TIMEOUT_S = 180.0  # bumped back from 60s for gemini thinking-mode rerun
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages),
                timeout=max(VLM_TIMEOUT_S, float(getattr(vlm, "timeout", VLM_TIMEOUT_S))),
            )
        except asyncio.TimeoutError:
            log.warning("round %d: VLM timeout", round_idx)
            (workspace / f"round_{round_idx:03d}_transport_error.json").write_text(
                json.dumps({"error_type":"TimeoutError", "scored":False}), encoding="utf-8")
            raise
        except Exception as e:  # noqa: BLE001
            (workspace / f"round_{round_idx:03d}_transport_error.json").write_text(
                json.dumps({"error_type":type(e).__name__, "scored":False}), encoding="utf-8")
            raise
        log.info("round %d: VLM %.1fs", round_idx, time.time() - t0)

        (workspace / f"round_{round_idx:03d}_response.txt").write_text(response, encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_api_metadata.json").write_text(
            json.dumps(vlm_call_info(vlm), indent=2), encoding="utf-8")
        raw_reply = getattr(vlm, "last_response", None)
        if isinstance(raw_reply, dict):
            (workspace / f"round_{round_idx:03d}_api_response.json").write_text(
                json.dumps(raw_reply, indent=2, ensure_ascii=False), encoding="utf-8")
        thought = extract_thought(response)
        action = extract_action(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(thought, encoding="utf-8")

        if not action or "cmd" not in action:
            history.append({"round": round_idx, "thought": thought, "parse_error": True,
                            "obs_meta": obs_meta})
            continue

        action_log = await _apply_action(engine, action)
        (workspace / f"round_{round_idx:03d}_action.json").write_text(
            json.dumps(action, indent=2), encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_actionlog.json").write_text(
            json.dumps(action_log, indent=2), encoding="utf-8")

        post_shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)
        prev_shot = post_shot

        history.append({"round": round_idx, "thought": thought,
                        "action": action, "action_log": action_log,
                        "obs_meta": obs_meta,
                        "screenshot": post_shot.name if post_shot else None})

        if action.get("cmd") == "probe" and action_log.get("label"):
            await _restore_probe(engine, action_log["label"],
                                 action_log.get("pre_position"))

        if action.get("cmd") == "stop":
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

    log.info("settling physics + verifying ...")
    verdict = await settle_and_verify(engine, params)
    result = "PASS" if verdict.get("passed") else "FAIL"
    log.info("%s — %s (%s)", run_id, result, verdict.get("reason"))

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": harness_id,
        "params": params,
        "max_rounds": max_rounds, "rounds": len(history),
        "early_stopped": early_stopped, "early_stop_reason": early_stop_reason,
        "verdict": verdict, "result": result,
        "history": history,
        "cube_label": cube_label, "platform_label": plat_label,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return EpisodeResult(
        run_id=run_id, model=vlm.model, seed=seed, result=result,
        rounds=len(history), verdict=verdict,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["EpisodeResult", "run_episode"]
