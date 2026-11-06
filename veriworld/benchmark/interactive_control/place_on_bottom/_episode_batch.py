"""Placement loop with configurable batched actions.

Scene setup, primitive action semantics, and verification are shared with
the single-command loop. The retained batch panel used three rounds,
six total actions, and a cap of five actions per round. Its single-command
comparison used eight rounds; these are distinct recorded budgets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt
from veriworld.infra.interactive.engine import InteractiveEngine

from ._response_diagnostics import vlm_call_info
from .generate_params import generate as generate_params
from ._episode import EpisodeResult, _apply_action, _restore_probe, _summarize_action
from ._shared import (
    _JSON_FENCE,
    extract_thought,
    open_level_and_setup,
    settle_and_verify,
    take_screenshot,
)

log = logging.getLogger(__name__)


def extract_actions(text: str, cap: int) -> Optional[list]:
    """Parse ``{"actions": [...]}`` (preferred), a bare JSON list of
    actions, or a single ``{"cmd": ...}`` dict (wrapped into a
    one-element list, so single-style replies still count as a
    1-action batch rather than a parse failure). Truncates to ``cap``.

    Falls back to fence-less JSON: some models (e.g. gpt-5-mini) emit
    the exact requested object without a markdown fence. The fallback
    only rescues responses that are already valid JSON, so it is a
    no-op for every episode the fenced path parsed."""
    candidates = []
    for m in _JSON_FENCE.finditer(text):
        try:
            candidates.append(json.loads(m.group(1)))
        except Exception:
            continue
    if not candidates:
        stripped = text.strip()
        if stripped[:1] in "{[":
            try:
                candidates.append(json.loads(stripped))
            except Exception:
                pass
    if not candidates:
        m = re.search(r'\{\s*"actions"\s*:\s*\[[\s\S]*?\]\s*\}', text)
        if m:
            try:
                candidates.append(json.loads(m.group(0)))
            except Exception:
                pass
    for d in candidates:
        if isinstance(d, dict) and isinstance(d.get("actions"), list):
            acts = [a for a in d["actions"] if isinstance(a, dict) and "cmd" in a]
            if acts:
                return acts[:cap]
        if isinstance(d, list):
            acts = [a for a in d if isinstance(a, dict) and "cmd" in a]
            if acts:
                return acts[:cap]
        if isinstance(d, dict) and "cmd" in d:
            return [d]
    return None


async def run_episode_batch(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    harness_id: str,
    system_prompt: str,
    observation_fn: Callable[[InteractiveEngine, dict, Optional[Path]], Awaitable[tuple]],
    package: str,
    max_rounds: int = 3,
    max_total_actions: int = 6,
    batch_cap: int = 5,
) -> EpisodeResult:
    params = generate_params(seed)
    cube_label = "PLACE_OBJ_" + params["cube"]["obj_label"].split("_")[-1]
    plat_label = "PLACE_OBJ_" + params["platform"]["obj_label"].split("_")[-1]

    run_id = f"{vlm.model}_seed{seed}_{harness_id}_r{max_rounds}a{max_total_actions}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": harness_id, "max_rounds": max_rounds,
        "max_total_actions": max_total_actions, "batch_cap": batch_cap,
        "action_span": "batch",
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=package, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "max_rounds": max_rounds,
                       "max_total_actions": max_total_actions, "batch_cap": batch_cap},
        extra_config=cfg,
    )

    await open_level_and_setup(engine, params)
    await asyncio.sleep(0.5)

    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None
    actions_used = 0

    init_shot = await take_screenshot(engine, "00_initial", workspace)
    prev_shot: Optional[Path] = init_shot

    SYSTEM = system_prompt.format(
        max_rounds=max_rounds, max_total_actions=max_total_actions,
        batch_cap=batch_cap)

    for round_idx in range(1, max_rounds + 1):
        if actions_used >= max_total_actions:
            break
        remaining = max_total_actions - actions_used
        last = history[-1].get("action_logs") if history else None
        last_summary = ("; ".join(_summarize_action(a) for a in last)
                        if last else None)

        parts, image_refs, obs_meta = await observation_fn(engine, params, prev_shot)
        if harness_id in ("vision", "combined", "vision_batch") and not image_refs:
            raise RuntimeError("Required current image is missing; episode is unscored")
        if len(obs_meta.get("state", [])) != params["n_total_objects"]:
            raise RuntimeError("Incomplete runtime scene state; episode is unscored")
        tail = (f"**Round {round_idx}/{max_rounds}** - action budget: "
                f"{remaining} of {max_total_actions} actions remaining.\n\n"
                f"Emit ONE JSON fence: {{\"actions\": [ ... ]}} with 1-"
                f"{min(batch_cap, remaining)} actions (probe / move_to / stop), "
                f"executed strictly in order. After moving the cube onto the "
                f"platform, include a final stop action.")
        if last_summary:
            tail += f"\n\nLast round's actions: {last_summary}"
        parts.append({"type": "text", "text": tail})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

        VLM_TIMEOUT_S = 180.0
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
        actions = extract_actions(response, cap=min(batch_cap, remaining))
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(thought, encoding="utf-8")

        if not actions:
            history.append({"round": round_idx, "thought": thought, "parse_error": True,
                            "obs_meta": obs_meta})
            continue

        # Apply the batch strictly in order. Probes stay lifted for the
        # remainder of the round (their reveal appears in the round's
        # closing screenshot, exactly as in single-span) and are all
        # restored after that screenshot.
        action_logs: list = []
        probed: list = []
        stopped_mid_batch = False
        for action in actions:
            a_log = await _apply_action(engine, action)
            action_logs.append(a_log)
            actions_used += 1
            if action.get("cmd") == "probe" and a_log.get("label"):
                probed.append((a_log["label"], a_log.get("pre_position")))
            if action.get("cmd") == "stop":
                stopped_mid_batch = True
                break

        (workspace / f"round_{round_idx:03d}_actions.json").write_text(
            json.dumps(actions, indent=2), encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_actionlogs.json").write_text(
            json.dumps(action_logs, indent=2), encoding="utf-8")

        post_shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)
        prev_shot = post_shot

        history.append({"round": round_idx, "thought": thought,
                        "actions": actions, "action_logs": action_logs,
                        "actions_used_total": actions_used,
                        "obs_meta": obs_meta,
                        "screenshot": post_shot.name if post_shot else None})

        for label, pre in probed:
            await _restore_probe(engine, label, pre)

        if stopped_mid_batch:
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

    log.info("settling physics + verifying ...")
    verdict = await settle_and_verify(engine, params)
    result = "PASS" if verdict.get("passed") else "FAIL"
    log.info("%s - %s (%s)", run_id, result, verdict.get("reason"))

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": harness_id, "action_span": "batch",
        "params": params,
        "max_rounds": max_rounds, "rounds": len(history),
        "max_total_actions": max_total_actions, "batch_cap": batch_cap,
        "actions_used": actions_used,
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


__all__ = ["run_episode_batch", "extract_actions"]
