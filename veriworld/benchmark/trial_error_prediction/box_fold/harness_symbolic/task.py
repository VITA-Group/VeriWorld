"""box_fold — SYMBOLIC harness.

The agent never sees a screenshot. Instead each round it gets:

* the static net topology (face labels + parent/child/edge per hinge,
  with the edge length S)
* the CURRENT face-center positions in world coordinates (updated
  after every applied fold)
* the canonical closed-cube target centers it should reach

It emits ONE fold per round in the same JSON-fence format used by
the vision harness. Verification reads back the actor positions
from UE and compares to the closed-cube targets with a small
tolerance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt
from veriworld.infra.interactive.engine import InteractiveEngine

from ..generate_params import generate as generate_params
from .._shared import (
    QUERY_STATE_CODE,
    expected_closed_centers,
    extract_action, extract_thought,
    fold_code,
    format_hinge_table,
    open_level_and_setup,
    parse_states,
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
You are an agent folding a flat cube net back into a closed cube,
working from numeric coordinates only — no images.

The 6 thin slab faces start lying flat on a table at z = table_z +
T/2. Each face is a square of side S = {S} cm and slab thickness
T = {T} cm. The unfolding shape used in this episode is
"{unfolding_name}". All folds rotate around hinges expressed in
the parent face's local frame, so a fold rotates the child face
PLUS every face attached to it (its subtree).

Hinges in this seed's spanning tree:
{hinge_block}

Hinge-axis convention in the parent's LOCAL frame:
  edge 'top' / 'bottom' -> rotation axis = parent local +X
  edge 'left' / 'right' -> rotation axis = parent local +Y
A positive angle is a right-handed rotation around that axis. The
sign that lifts the child UPWARD depends on which side of the
parent the child sits — that's the puzzle.

ACTION FORMAT — emit ONE JSON code fence per round:

```json
{{"cmd": "fold", "hinge_id": 2, "angle": 90}}
{{"cmd": "stop"}}
```

`angle` is in degrees (any real value).

GOAL: every face center within {tol_cm} cm of its canonical
closed-cube target, listed below.

Closed-cube targets (label : x, y, z, in cm):
{targets_block}

You have {max_rounds} rounds. Emit `stop` once you believe the
cube is closed.
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


def _format_states(states: list[dict]) -> str:
    lines = []
    for s in states:
        x, y, z = s["position"]
        lines.append(f"  face {s['index']} ({s['label']:>6s}): "
                     f"x={x:8.1f}  y={y:8.1f}  z={z:8.1f}")
    return "\n".join(lines)


def _format_targets(params: dict) -> str:
    expected = expected_closed_centers(params)
    order = ["bottom", "front", "back", "left", "right", "top"]
    label_to_idx = {f["label"]: f["index"] for f in params["faces"]}
    lines = []
    for label in order:
        ex, ey, ez = expected[label]
        idx = label_to_idx[label]
        lines.append(f"  face {idx} ({label:>6s}): "
                     f"x={ex:8.1f}  y={ey:8.1f}  z={ez:8.1f}")
    return "\n".join(lines)


async def _read_states(engine) -> list[dict]:
    state_out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    return parse_states(state_out)


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

    run_id = f"{vlm.model}_seed{seed}_box_fold_symbolic_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "symbolic", "max_rounds": max_rounds,
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
        T=params["T"],
        tol_cm=params["verification"]["tolerance_cm"],
        max_rounds=max_rounds,
        unfolding_name=params["unfolding_name"],
        hinge_block=format_hinge_table(params),
        targets_block=_format_targets(params),
    )

    history: list = []
    early_stopped = False
    early_stop_reason: Optional[str] = None

    init_states = await _read_states(engine)
    (workspace / "00_initial_states.json").write_text(
        json.dumps(init_states, indent=2), encoding="utf-8")
    cur_states_block = _format_states(init_states)
    consecutive_timeouts = 0
    MAX_CONSECUTIVE_TIMEOUTS = 3

    for round_idx in range(1, max_rounds + 1):
        last = history[-1].get("action_log") if history else None
        text = (
            f"**Round {round_idx}/{max_rounds}**\n\n"
            f"Current face centers (world coordinates, cm):\n"
            f"{cur_states_block}\n\n"
            f"Emit ONE fold (or stop)."
        )
        if last:
            text += (
                f"\n\n**Last action:** {last.get('cmd', '?')}"
                + (f" hinge={last.get('hinge_id')} angle={last.get('angle')}"
                   if last.get('cmd') == 'fold' else "")
                + (f" — {last.get('ue_out','')}" if last.get('ue_out') else "")
                + (f"\n  error: {last['error']}" if last.get('error') else "")
            )

        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text},
        ]
        (workspace / f"round_{round_idx:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, []), encoding="utf-8")

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

        post_states = await _read_states(engine)
        (workspace / f"round_{round_idx:03d}_states.json").write_text(
            json.dumps(post_states, indent=2), encoding="utf-8")
        cur_states_block = _format_states(post_states)

        history.append({"round": round_idx, "thought": thought,
                        "action": action, "action_log": action_log,
                        "states": post_states})

        if action.get("cmd") == "stop":
            early_stopped = True
            early_stop_reason = "agent_stop"
            break

    # Guard against a dead UE post-episode (e.g. after 3-strikes timeout)
    # so this call can't hang the orchestrator on a closed websocket.
    try:
        final_states = await asyncio.wait_for(_read_states(engine), timeout=30.0)
        verdict = verify_states(final_states, params)
    except Exception as error:
        raise RuntimeError('BoxFold final state unavailable; episode remains unscored') from error
    result = "PASS" if verdict.get("passed") else "FAIL"
    log.info("%s — %s (%s)", run_id, result, verdict.get("reason"))

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "symbolic",
        "params": params,
        "max_rounds": max_rounds, "rounds": len(history),
        "early_stopped": early_stopped, "early_stop_reason": early_stop_reason,
        "verdict": verdict, "result": result,
        "history": history,
        "final_states": final_states,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return EpisodeResult(
        run_id=run_id, model=vlm.model, seed=seed, result=result,
        rounds=len(history), verdict=verdict,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
