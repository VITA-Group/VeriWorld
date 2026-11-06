"""box_fold — VISION harness.

Per round the agent sees a current screenshot of the cube net (at
whatever folded state the previous actions left it). The agent emits
ONE fold command, the harness applies it, then verifies if all 6
faces are at their canonical closed-cube positions.

The unfolding shape is randomized per seed (T-cross / plus / left-T
/ right-T / 4-strip / Z-shape), so the prompt's hinge table is
generated from the seed-specific tree at runtime.

Action format (one JSON code fence per round):

  ```json
  {"cmd": "fold", "hinge_id": 2, "angle": 90}
  {"cmd": "stop"}
  ```
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from ..generate_params import generate as generate_params
from .._shared import (
    QUERY_STATE_CODE,
    extract_action, extract_thought,
    fold_code,
    format_flat_layout, format_hinge_table,
    open_level_and_setup,
    parse_states,
    take_screenshot,
    unwrap,
    verify_states,
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


SYSTEM_PROMPT = """\
You are an agent folding a flat cube net back into a closed cube.
The scene shows 6 thin slab faces lying flat on the ground.
The specific unfolding shape used here is "{unfolding_name}".

Flat layout (face centers as (Δx, Δy) in units of S relative to
the root face 0 = bottom):
{flat_block}

Hinges in this seed's spanning tree:
{hinge_block}

A fold rotates the CHILD face AND every face attached to its
subtree as a rigid unit (so if face A is hinged to B, and B is
hinged to C, folding A->B also moves C with B).

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{{"cmd": "fold", "hinge_id": 2, "angle": 90}}
{{"cmd": "stop"}}
```

`angle` is in degrees. Folds are around the parent face's CURRENT
local frame, so:
  - for edges 'top' / 'bottom' the rotation axis is parent's local +X
  - for edges 'left' / 'right' the rotation axis is parent's local +Y
  - +90 / -90 either lifts the child up or pushes it down depending
    on which side of the parent the child sits — that's the puzzle.

GOAL: after your folds, all 6 faces should sit at the 6 face-
centers of a closed cube with side S = {S} cm centered at ({cx},
{cy}, table_z + S/2).

PASS condition: every face center within {tol_cm} cm of its
canonical closed-cube position.

You have {max_rounds} rounds total. Emit `stop` once the cube looks
closed.
"""


async def _apply_action(engine, action: dict) -> dict:
    cmd = action.get("cmd")
    out = {"cmd": cmd}
    if cmd == "fold":
        try:
            hinge_id = int(action.get("hinge_id", -1))
            angle = float(action.get("angle", 0.0))
        except (TypeError, ValueError):
            return {**out, "error": "bad hinge_id or angle"}
        if not (0 <= hinge_id <= 4):
            return {**out, "error": f"hinge_id {hinge_id} out of [0,4]"}
        ue_out = unwrap(await engine.python_exec(fold_code(hinge_id, angle)))
        if "FOLDED hinge_id=" not in ue_out:
            raise RuntimeError("BoxFold action acknowledgment missing")
        out["hinge_id"] = hinge_id
        out["angle"] = angle
        out["ue_out"] = ue_out.strip().split("\n")[-1] if ue_out else ""
    elif cmd == "stop":
        pass
    else:
        out["error"] = f"unknown cmd {cmd!r}"
    return out


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_rounds: Optional[int] = None,
) -> EpisodeResult:
    params = generate_params(seed)
    if max_rounds is None:
        max_rounds = int(params["verification"]["max_rounds"])

    run_id = f"{vlm.model}_seed{seed}_box_fold_vision_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "vision", "max_rounds": max_rounds,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "max_rounds": max_rounds},
        extra_config=cfg,
    )

    await open_level_and_setup(engine, params)
    await asyncio.sleep(0.5)

    SYSTEM = SYSTEM_PROMPT.format(
        S=params["S"],
        cx=params["spawn_xy"][0],
        cy=params["spawn_xy"][1],
        tol_cm=params["verification"]["tolerance_cm"],
        max_rounds=max_rounds,
        unfolding_name=params["unfolding_name"],
        flat_block=format_flat_layout(params),
        hinge_block=format_hinge_table(params),
    )

    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None

    init_shot = await take_screenshot(engine, "00_initial", workspace)
    prev_shot: Optional[Path] = init_shot
    consecutive_timeouts = 0
    MAX_CONSECUTIVE_TIMEOUTS = 3

    for round_idx in range(1, max_rounds + 1):
        parts: list = []
        image_refs: list[str] = []
        if prev_shot and prev_shot.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(prev_shot)}})
            image_refs.append(prev_shot.name)
        last = history[-1].get("action_log") if history else None
        text = (f"**Round {round_idx}/{max_rounds}**\n\n"
                f"Image: current state of the cube net. Emit ONE fold (or stop).\n")
        if last:
            text += (f"\n**Last action:** {last.get('cmd', '?')}"
                     + (f" hinge={last.get('hinge_id')} angle={last.get('angle')}"
                        if last.get('cmd') == 'fold' else "")
                     + (f" — {last.get('ue_out','')}" if last.get('ue_out') else "")
                     + (f"\n  error: {last['error']}" if last.get('error') else ""))
        parts.append({"type": "text", "text": text})

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": parts},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, image_refs), encoding="utf-8")

        VLM_TIMEOUT_S = max(180.0, float(getattr(vlm, "timeout", 180.0)))
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages),
                timeout=VLM_TIMEOUT_S,
            )
        except asyncio.TimeoutError as error:
            raise RuntimeError('BoxFold model API timed out; episode remains unscored') from error
        except Exception as error:
            raise RuntimeError('BoxFold model API failed; episode remains unscored') from error
        consecutive_timeouts = 0
        log.info("round %d: VLM %.1fs", round_idx, time.time() - t0)

        (workspace / f"round_{round_idx:03d}_response.txt").write_text(response, encoding="utf-8")
        thought = extract_thought(response)
        action = extract_action(response)
        (workspace / f"round_{round_idx:03d}_thought.txt").write_text(thought, encoding="utf-8")

        if not action or "cmd" not in action:
            history.append({"round": round_idx, "thought": thought, "parse_error": True})
            continue

        action_log = await _apply_action(engine, action)
        (workspace / f"round_{round_idx:03d}_action.json").write_text(
            json.dumps(action, indent=2), encoding="utf-8")
        (workspace / f"round_{round_idx:03d}_actionlog.json").write_text(
            json.dumps(action_log, indent=2), encoding="utf-8")

        post_shot = await take_screenshot(engine, f"round_{round_idx:03d}", workspace)
        if post_shot and post_shot.exists():
            prev_shot = post_shot

        history.append({"round": round_idx, "thought": thought,
                        "action": action, "action_log": action_log,
                        "screenshot": post_shot.name if post_shot else None})

        if action.get("cmd") == "stop":
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

    # Read final positions + verify. Guard against a dead UE (e.g. after
    # 3-strikes-timeout path or a mid-episode crash) — without this, the
    # websocket call here can hang the whole orchestrator forever waiting
    # on a UE that no longer exists.
    try:
        state_out = unwrap(await asyncio.wait_for(
            engine.python_exec(QUERY_STATE_CODE), timeout=30.0))
        states = parse_states(state_out)
        verdict = verify_states(states, params)
    except Exception as error:
        raise RuntimeError('BoxFold final state unavailable; episode remains unscored') from error
    result = "PASS" if verdict.get("passed") else "FAIL"
    log.info("%s — %s (%s)", run_id, result, verdict.get("reason"))

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "vision",
        "params": params,
        "max_rounds": max_rounds, "rounds": len(history),
        "early_stopped": early_stopped, "early_stop_reason": early_stop_reason,
        "verdict": verdict, "result": result,
        "history": history,
        "final_states": states,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return EpisodeResult(
        run_id=run_id, model=vlm.model, seed=seed, result=result,
        rounds=len(history), verdict=verdict,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
