"""moving_shooter — SYMBOLIC harness.

The agent gets the motion equations + a HIT / MISS log from each
round, but NO video. The symbolic equations are written in a
hidden ROTATED frame (frame_rotation_deg ∈ [-140, -40] ∪ [40, 140]),
so a model that solves only from equations and never visually
grounds them will compute aim_yaw in the wrong frame and miss.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient
from veriworld.infra.interactive.engine import InteractiveEngine

from ..generate_params import generate as generate_params, equations_text
from .._shared import (VLM_TIMEOUT_S, is_truncated, open_level_and_setup, 
                       parse_response, run_round, vlm_call_info, 
                       write_episode_stub)

log = logging.getLogger(__name__)


@dataclass
class EpisodeResult:
    run_id: str
    model: str
    seed: int
    result: str
    rounds: int
    final_status: str
    artifacts: dict = field(default_factory=dict)


SYSTEM_PROMPT = """\
You are an agent aiming an orbiting shooter at a moving target. You
have the motion equations and a per-round HIT/MISS log — NO video.

Each round, decide:
  fire_time  (0-10 seconds)  — when to fire
  aim_yaw    (degrees)       — absolute world-frame horizontal direction
  aim_pitch  (degrees)       — absolute barrel tilt (0 = horizontal)

Reply in this EXACT format:
```
analysis: <your reasoning>
knowledge: <what you've determined>
fire_time: <number 0-10>
aim_yaw: <number in degrees>
aim_pitch: <number in degrees>
```
IMPORTANT: keep analysis/observation and knowledge to AT MOST a
few sentences each — do NOT write long derivations. Your reply
MUST END with the three numeric lines (fire_time / aim_yaw /
aim_pitch); a reply without them scores as a wasted round.
"""




async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_rounds: int = 5,
) -> EpisodeResult:
    params = generate_params(seed)
    eqs = equations_text(params)
    run_id = f"{vlm.model}_seed{seed}_moving_shooter_symbolic_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "params.json").write_text(json.dumps(params, indent=2),
                                            encoding="utf-8")
    (workspace / "equations.md").write_text(eqs, encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "max_rounds": max_rounds},
        extra_config={"run_id": run_id, "harness": "symbolic",
                      "max_rounds": max_rounds},
    )

    write_episode_stub(workspace, run_id=run_id, model=vlm.model, seed=seed,
                       harness="symbolic", params=params,
                       max_rounds=max_rounds)

    await open_level_and_setup(engine, params)
    await asyncio.sleep(1.0)

    trajectory: list[dict] = []
    final_status = "MISS"

    for round_num in range(1, max_rounds + 1):
        log.info("seed=%d round %d/%d", seed, round_num, max_rounds)
        log_block_lines = ["# Knowledge"]
        for t in trajectory:
            # ``.get`` on params, not ``[]``: the VLM-timeout and
            # VLM-error records carry no "params" key, so indexing here
            # turns one flaky API call into a KeyError that kills the
            # whole episode on the following round.
            tp = t.get("params", {})
            log_block_lines.append(
                f"- R{t['round']}: {t.get('result', '?')} "
                f"ft={tp.get('fire_time')} "
                f"yaw={tp.get('aim_yaw')} "
                f"pitch={tp.get('aim_pitch')}"
            )
            if t.get("log"):
                log_block_lines.append(f"  ```\n{t['log'][:600]}\n  ```")
            if t.get("knowledge"):
                log_block_lines.append(f"  > {t['knowledge'][:200]}")
        knowledge_md = "\n".join(log_block_lines)

        full_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Round {round_num}/{max_rounds}.\n\n"
            f"{eqs}\n\n"
            f"{knowledge_md}\n\n"
            f"Pick fire_time, aim_yaw, aim_pitch."
        )
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            full_prompt, encoding="utf-8")

        loop = asyncio.get_event_loop()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": full_prompt}]}]
        t0 = time.time()
        try:
            response = await asyncio.wait_for(
                loop.run_in_executor(None, vlm.chat, messages),
                timeout=max(VLM_TIMEOUT_S, float(getattr(vlm, "timeout", VLM_TIMEOUT_S))),
            )
        except asyncio.TimeoutError:
            log.warning("round %d: VLM timeout", round_num)
            trajectory.append({"round": round_num, "timeout": True})
            (workspace / f"round_{round_num:02d}_transport_error.json").write_text(
                json.dumps({"error_type":"TimeoutError", "scored":False}), encoding="utf-8")
            raise
        except Exception as e:  # noqa: BLE001
            log.error("round %d: VLM error: %s", round_num, e)
            trajectory.append({"round": round_num, "vlm_error": str(e)[:200]})
            (workspace / f"round_{round_num:02d}_transport_error.json").write_text(
                json.dumps({"error_type":type(e).__name__, "scored":False}), encoding="utf-8")
            raise
        log.info("round %d: VLM %.1fs", round_num, time.time() - t0)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(
            response, encoding="utf-8")
        analysis, knowledge, ft, aim_yaw, aim_pitch = parse_response(response)

        if ft is None or aim_yaw is None or aim_pitch is None:
            info = vlm_call_info(vlm)
            log.warning("round %d: parse error%s", round_num,
                        " (TRUNCATED at token limit)" if is_truncated(info)
                        else "")
            trajectory.append({
                "round": round_num, "analysis": analysis,
                "knowledge": knowledge, "result": "PARSE_ERROR",
                "truncated": is_truncated(info), "vlm": info,
                "params": {"fire_time": ft, "aim_yaw": aim_yaw,
                           "aim_pitch": aim_pitch},
            })
            continue

        rd = await run_round(engine, params, ft, aim_yaw, aim_pitch,
                             run_id=round_num, workspace=workspace,
                             record=False)
        trajectory.append({
            "round": round_num,
            "analysis": analysis, "knowledge": knowledge,
            "params": {"fire_time": ft, "aim_yaw": aim_yaw,
                       "aim_pitch": aim_pitch},
            "result": rd["result"], "ticks": rd.get("ticks", 0),
            "log": rd.get("log", ""),
        })
        if rd["result"] == "HIT":
            final_status = "HIT"
            break

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "symbolic", "params": params,
        "max_rounds": max_rounds, "rounds": len(trajectory),
        "final_status": final_status, "crashed": False,
        "trajectory": trajectory,
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")

    return EpisodeResult(
        run_id=run_id, model=vlm.model, seed=seed,
        result=final_status, rounds=len(trajectory),
        final_status=final_status,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult"]
