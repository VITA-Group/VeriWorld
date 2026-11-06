"""moving_shooter — SYMBOLIC + natural-prose padding (padded-S control).

Identical to harness_symbolic in every scored respect — same SYSTEM_PROMPT
(imported), same equations, same knowledge log, same parser, same round
loop — plus one block of deterministic, coherent, task-irrelevant prose
("Auxiliary context"). The filler generator is imported from
place_on_platform.harness_symbolic_padded_natural, the exact generator
that ran the 10-model PlaceOnBottom control, so the two tasks' padded
arms are directly comparable.

pad_words=1000 ≈ 1.3k tokens, sized to the combined arm's extra channel
(a 1920x960 frame grid costs ~1.1-1.4k image tokens on the panel's
transports).

Discriminative logic: if C < S on this task is driven by context length
or irrelevant extra information, padded-S must degrade equally; if it is
driven by visual-channel integration, padded-S stays at S.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from veriworld.common import RunLogger, VLMClient
from veriworld.infra.interactive.engine import InteractiveEngine

from ..generate_params import generate as generate_params, equations_text
from .._shared import (VLM_TIMEOUT_S, is_truncated, open_level_and_setup,
                       parse_response, run_round, vlm_call_info,
                       write_episode_stub)
from ..harness_symbolic.task import EpisodeResult, SYSTEM_PROMPT
from .._padding import (
    make_filler_natural)

log = logging.getLogger(__name__)


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_rounds: int = 5,
    pad_words: int = 1000,
) -> EpisodeResult:
    params = generate_params(seed)
    eqs = equations_text(params)
    filler = make_filler_natural(seed, pad_words)
    run_id = (f"{vlm.model}_seed{seed}_moving_shooter_sympad{pad_words}"
              f"_r{max_rounds}")
    workspace = logger.model_dir(run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "params.json").write_text(json.dumps(params, indent=2),
                                           encoding="utf-8")
    (workspace / "equations.md").write_text(eqs, encoding="utf-8")
    (workspace / "filler.txt").write_text(filler, encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model,
                       "max_rounds": max_rounds, "pad_words": pad_words},
        extra_config={"run_id": run_id, "harness": "symbolic_padded",
                      "max_rounds": max_rounds, "pad_words": pad_words},
    )

    write_episode_stub(workspace, run_id=run_id, model=vlm.model, seed=seed,
                       harness="symbolic_padded", params=params,
                       max_rounds=max_rounds)

    await open_level_and_setup(engine, params)
    await asyncio.sleep(1.0)

    trajectory: list[dict] = []
    final_status = "MISS"

    for round_num in range(1, max_rounds + 1):
        log.info("seed=%d round %d/%d", seed, round_num, max_rounds)
        log_block_lines = ["# Knowledge"]
        for t in trajectory:
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

        # Only difference vs harness_symbolic: the Auxiliary context block,
        # in the same position place_on_platform's padded arm puts it
        # (before the task content).
        full_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Round {round_num}/{max_rounds}.\n\n"
            f"Auxiliary context:\n{filler}\n\n"
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
        "harness": "symbolic_padded", "pad_words": pad_words,
        "params": params, "max_rounds": max_rounds,
        "rounds": len(trajectory), "final_status": final_status,
        "crashed": False, "trajectory": trajectory,
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
