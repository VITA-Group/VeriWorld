"""moving_shooter — VISION harness, FRAME-GRID feedback (uniform channel).

Sibling of harness_vision along the visual-transport axis. The original
harness attaches the raw round-replay video, which only Gemini-family
transports accept; every other model silently degrades to text-only —
i.e., its "V" condition is not visual at all. This variant converts each
round's replay into a 2x3 grid of six evenly-sampled frames and sends
it through the ordinary image-chat path, so ALL models receive an
identical visual channel (the LightWar frame-stack pattern, paper Fig. 4).

Everything else — round-0 observe, response format, parse, simulation,
verifier, budget — is identical to harness_vision.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from ..generate_params import generate as generate_params
from .._shared import (VLM_TIMEOUT_S, is_truncated, open_level_and_setup, 
                       run_round, vlm_call_info, write_episode_stub)
from .._frames import FrameTransportError, video_to_grid

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
have visual feedback only — NO equations.

Each round you receive a single image: a 2x3 grid of six frames
sampled evenly, in time order (left-to-right, then top-to-bottom),
from the replay of the previous round. Use it to infer the target's
and shooter's motion, then decide:
  fire_time  (0-10 seconds)  — when to fire
  aim_yaw    (degrees)       — absolute world-frame horizontal direction
  aim_pitch  (degrees)       — absolute barrel tilt (0 = horizontal)

Reply in this EXACT format:
```
observation: <what you see>
knowledge: <what you've learned>
fire_time: <number 0-10>
aim_yaw: <number in degrees>
aim_pitch: <number in degrees>
```
IMPORTANT: keep analysis/observation and knowledge to AT MOST a
few sentences each — do NOT write long derivations. Your reply
MUST END with the three numeric lines (fire_time / aim_yaw /
aim_pitch); a reply without them scores as a wasted round.
"""




async def _ask_with_frames(vlm: VLMClient, prompt: str,
                           video_path: Optional[Path],
                           grid_png: Path) -> tuple[str, bool]:
    """Send prompt + frame grid (if extractable). Returns (response,
    grid_attached)."""
    loop = asyncio.get_event_loop()
    grid: Optional[Path] = None
    if video_path is not None and Path(video_path).exists():
        grid = await loop.run_in_executor(
            None, video_to_grid, Path(video_path), grid_png)
    if grid is None:
        raise FrameTransportError("A required replay frame grid is missing; episode is unscored.")
    parts: list = []
    if grid is not None:
        parts.append({"type": "image_url",
                      "image_url": {"url": png_to_base64_url(grid)}})
    parts.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": parts}]
    resp = await asyncio.wait_for(
        loop.run_in_executor(None, vlm.chat, messages),
        timeout=max(VLM_TIMEOUT_S, float(getattr(vlm, "timeout", VLM_TIMEOUT_S))),
    )
    return resp, grid is not None


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    max_rounds: int = 5,
) -> EpisodeResult:
    params = generate_params(seed)
    run_id = f"{vlm.model}_seed{seed}_moving_shooter_visionframes_r{max_rounds}"
    workspace = logger.model_dir(run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "params.json").write_text(json.dumps(params, indent=2),
                                            encoding="utf-8")
    logger.snapshot_model(
        task_module=__package__, model_name=run_id, seed=seed,
        resolved_args={"seed": seed, "model": vlm.model, "max_rounds": max_rounds},
        extra_config={"run_id": run_id, "harness": "vision_frames",
                      "max_rounds": max_rounds},
    )

    from .._shared import parse_response

    write_episode_stub(workspace, run_id=run_id, model=vlm.model, seed=seed,
                       harness="vision_frames", params=params,
                       max_rounds=max_rounds)

    await open_level_and_setup(engine, params)
    await asyncio.sleep(1.0)

    log.info("seed=%d round 0 (observe)", seed)
    obs = await run_round(engine, params, fire_time=-1.0, aim_yaw=0.0,
                          aim_pitch=0.0, run_id=0, workspace=workspace,
                          record=True)
    last_video: Optional[Path] = obs.get("video_path")

    trajectory: list[dict] = []
    final_status = "MISS"

    for round_num in range(1, max_rounds + 1):
        log.info("seed=%d round %d/%d", seed, round_num, max_rounds)
        knowledge_lines = ["# Knowledge"]
        for t in trajectory:
            # ``.get`` on params, not ``[]``: the VLM-timeout and
            # VLM-error records carry no "params" key, so indexing here
            # turns one flaky API call into a KeyError that kills the
            # whole episode on the following round.
            tp = t.get("params", {})
            knowledge_lines.append(
                f"- R{t['round']}: {t.get('result', '?')} "
                f"ft={tp.get('fire_time')} "
                f"yaw={tp.get('aim_yaw')} "
                f"pitch={tp.get('aim_pitch')}"
            )
            if t.get("knowledge"):
                knowledge_lines.append(f"  > {t['knowledge'][:200]}")
        knowledge_md = "\n".join(knowledge_lines)

        prompt = (
            f"Round {round_num}/{max_rounds}.\n\n"
            f"{knowledge_md}\n\n"
            f"Study the attached frame grid (six frames, time-ordered) and "
            f"choose fire_time, aim_yaw, aim_pitch."
        )
        full_prompt = SYSTEM_PROMPT + "\n\n" + prompt
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            full_prompt, encoding="utf-8")

        grid_png = workspace / f"round_{round_num:02d}_grid.png"
        t0 = time.time()
        try:
            response, grid_ok = await _ask_with_frames(
                vlm, full_prompt, last_video, grid_png)
        except FrameTransportError:
            raise
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
        log.info("round %d: VLM %.1fs (grid=%s)", round_num,
                 time.time() - t0, grid_ok)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(
            response, encoding="utf-8")
        observation, knowledge, ft, aim_yaw, aim_pitch = parse_response(response)

        if ft is None or aim_yaw is None or aim_pitch is None:
            info = vlm_call_info(vlm)
            log.warning("round %d: parse error%s", round_num,
                        " (TRUNCATED at token limit)" if is_truncated(info)
                        else "")
            trajectory.append({
                "round": round_num, "observation": observation,
                "knowledge": knowledge, "result": "PARSE_ERROR",
                "truncated": is_truncated(info), "vlm": info,
                "grid_attached": grid_ok,
                "params": {"fire_time": ft, "aim_yaw": aim_yaw,
                           "aim_pitch": aim_pitch},
            })
            continue

        rd = await run_round(engine, params, ft, aim_yaw, aim_pitch,
                             run_id=round_num, workspace=workspace,
                             record=True)
        last_video = rd.get("video_path") or last_video
        trajectory.append({
            "round": round_num,
            "observation": observation, "knowledge": knowledge,
            "grid_attached": grid_ok,
            "params": {"fire_time": ft, "aim_yaw": aim_yaw,
                       "aim_pitch": aim_pitch},
            "result": rd["result"], "ticks": rd.get("ticks", 0),
            "log": rd.get("log", ""),
            "video": str(rd["video_path"]) if rd.get("video_path") else None,
        })
        if rd["result"] == "HIT":
            final_status = "HIT"
            break

    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "harness": "vision_frames", "params": params,
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
