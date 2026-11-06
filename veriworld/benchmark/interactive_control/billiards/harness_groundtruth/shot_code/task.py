"""Billiards — harness_groundtruth / shot_code ablation.

Pure-symbolic counterpart to ``harness_visiongroundtruth``: the prompt
contains the **full** physics ground-truth state (static config + per-
round dynamic positions) but **NO image** is attached.

  * static scene config (table dims, rail heights, hole gap, ball
    radius, cue/ball damping, cue speed cap, hole positions)
  * per-round dynamic state (every alive ball's current (x, y, z),
    cue-scratch flag, alive count)

This isolates the contribution of the image: any score gap between
``harness_visiongroundtruth`` and ``harness_groundtruth`` is
attributable to vision (since the structured info is identical).

The post-shot screenshot is still HARVESTED to ``round_NN.png`` for
human inspection of results, but is NOT sent to the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from ..._transport import runtime_output, save_raw_response, require_state
from veriworld.common import RunLogger, VLMClient
from veriworld.infra.interactive.engine import InteractiveEngine

from ..._shared import (
    LABEL_PREFIX,
    assemble_setup,
    assemble_shot,
    extract_shot,
    extract_thought,
    parse_verify_log,
)
from .._common import harvest_screenshot, screenshot_dir_from_exe

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
HARNESS_DIR = HERE.parent
TASK_ROOT = HARNESS_DIR.parent

CONDITION_KEYS = {
    "max_rounds": "Number of shots the agent gets. Default 5.",
    "settle_timeout": "Seconds to wait for in-scene settle log per round. Default 35 (covers EXTENDED_CAP_S=25 + harvest write latency).",
    "difficulty": "'standard' (5 balls, 800x800 square — original) or 'hard' (10 balls, 1500x800 rectangle). Default 'standard'.",
}


@dataclass
class RoundEntry:
    round: int
    thought: str = ""
    shot: Optional[dict] = None
    verdict: Optional[str] = None
    alive_target: int = 5
    cue_pocketed: bool = False
    error: Optional[str] = None
    image: Optional[str] = None


@dataclass
class EpisodeResult:
    model: str
    seed: int
    result: str
    rounds_used: int
    final_pocketed: int
    cue_pocketed: bool
    trajectory: List[RoundEntry] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    exe: Path,
    max_rounds: int = 5,
    settle_timeout: Optional[float] = None,
    difficulty: str = "standard",
    **_unused: Any,
) -> EpisodeResult:
    from ...generate_params import generate as generate_params
    params = generate_params(seed=seed, difficulty=difficulty)
    target_total = params["num_target_balls"]
    # Per-difficulty default for settle polling. Hard's longer
    # EXTENDED_CAP_S (40s) needs at least 45s of polling slack;
    # standard's 25s cap is comfortably covered by 35s.
    if settle_timeout is None:
        settle_timeout = 50.0 if difficulty == "hard" else 35.0

    model_name = vlm.name if hasattr(vlm, "name") else vlm.model
    workspace = logger.model_dir(model_name)
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    (workspace / "lean_verify").mkdir(parents=True, exist_ok=True)

    logger.snapshot_model(
        task_module=__package__, model_name=model_name, seed=seed,
        resolved_args={"seed": seed, "model": model_name,
                       "max_rounds": max_rounds, "settle_timeout": settle_timeout,
                       "exe": str(exe)},
    )

    task_md = (TASK_ROOT / "task.md").read_text(encoding="utf-8")
    api_md = (HARNESS_DIR / "api.md").read_text(encoding="utf-8")
    example_code = (HARNESS_DIR / "example.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    shot_dir = screenshot_dir_from_exe(exe)
    trajectory: List[RoundEntry] = []
    last_image_path: Optional[Path] = None
    cumulative_pocketed = 0
    cue_scratch_count = 0

    try:
        await engine.switch_level("/Game/Levels/Untitled")
    except Exception as e:  # noqa: BLE001
        log.warning("level switch failed: %s", e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e
    await asyncio.sleep(2)

    # ── Round 0: build scene + initial screenshot ─────────────────────
    log.info("Round 0: scene setup + observation screenshot")
    if log_for_verify.exists(): log_for_verify.unlink()
    try:
        runtime_output(await engine.python_exec(assemble_setup(workspace)))
        await asyncio.sleep(2)   # let table render before snap

        observe_png = workspace / "round_00_observe.png"
        got = await harvest_screenshot(engine, shot_dir, observe_png)
        if got:
            last_image_path = observe_png
    except Exception as e:  # noqa: BLE001
        log.warning("Round 0 fault: %s: %s", type(e).__name__, e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e

    # ── Rounds 1..N ────────────────────────────────────────────────────
    for round_num in range(1, max_rounds + 1):
        log.info("Round %d/%d (cumulative pocketed=%d/%d)",
                 round_num, max_rounds, cumulative_pocketed, target_total)

        scoreboard = (
            f"## Scoreboard\n"
            f"Pocketed target balls so far: {cumulative_pocketed}/{target_total}\n"
            f"Cue scratches so far: {cue_scratch_count} "
            f"(scratching does not end the episode — the cue is teleported "
            f"back to the table centre and you fire from there next round)"
        )

        # Full ground-truth dump: static physics params (from params.json,
        # constant per episode) + dynamic per-round state (from state.json).
        cx, cy = params["table_center_xy"]
        hx = params["table_size_x"] / 2.0
        hy = params["table_size_y"] / 2.0
        gap = params["hole_corner_gap"]
        # Pocket centre estimates (camera-up = +X, camera-right = +Y):
        pockets = {
            "NE_pocket": [cx + hx - gap * 0.25, cy + hy - gap * 0.25],
            "NW_pocket": [cx - hx + gap * 0.25, cy + hy - gap * 0.25],
            "SE_pocket": [cx + hx - gap * 0.25, cy - hy + gap * 0.25],
            "SW_pocket": [cx - hx + gap * 0.25, cy - hy + gap * 0.25],
        }
        static_block = (
            "## Static scene parameters (constant per episode)\n"
            f"  table_center_xy   = {params['table_center_xy']}\n"
            f"  table_size_x      = {params['table_size_x']}  cm  (along world X = screen-up)\n"
            f"  table_size_y      = {params['table_size_y']}  cm  (along world Y = screen-right)\n"
            f"  table_top_z       = {params['table_top_z']}  cm\n"
            f"  rail_height       = {params['rail_height']}  cm\n"
            f"  rail_thickness    = {params['rail_thickness']}  cm\n"
            f"  hole_corner_gap   = {params['hole_corner_gap']}  cm  (corner pocket width)\n"
            f"  ball_radius       = {params['ball_radius']}  cm\n"
            f"  cue_linear_damp   = {params['cue_linear_damping']}\n"
            f"  cue_angular_damp  = {params['cue_angular_damping']}\n"
            f"  ball_linear_damp  = {params['ball_linear_damping']}\n"
            f"  ball_angular_damp = {params['ball_angular_damping']}\n"
            f"  cue_max_speed     = {params['cue_max_speed_cm_s']}  cm/s\n"
            "  pocket_centres    =\n"
        )
        for name, p in pockets.items():
            static_block += f"    {name}: x={p[0]:.0f}, y={p[1]:.0f}\n"

        dynamic_block = "## Live ball state (ground truth this round, world cm)\n"
        state_path = workspace / "state.json"
        require_state(state_path)
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                balls = state.get("balls", {})
                if balls:
                    for bid in sorted(balls.keys()):
                        x, y, z = balls[bid]
                        dynamic_block += f"  {bid}: x={x}, y={y}, z={z}\n"
                else:
                    dynamic_block += "  (no live balls remain)\n"
                dynamic_block += (
                    f"  alive_target = {state.get('alive_target', '?')}\n"
                    f"  cue_scratched_last_round = "
                    f"{'yes' if state.get('cue_scratched') else 'no'}\n"
                )
            except Exception as e:
                dynamic_block += f"  (state.json read failed: {e})\n"
        else:
            dynamic_block += "  (no state.json yet — first round)\n"

        prompt = (
            f"{task_md}\n\n{api_md}\n\n{scoreboard}\n\n"
            f"{static_block}\n{dynamic_block}\n"
            f"This is Round {round_num}/{max_rounds}. **No image is attached** "
            f"— the listings above give every quantitative parameter and the "
            f"exact (x, y, z) of each alive ball, which is the source of truth.\n\n"
            f"# Example shot (illustrative — tune)\n```python\n{example_code}\n```\n\n"
            f"Reply with:\n"
            f"thought: <describe positions and intent>\n\n"
            f'```json\n{{"angle_deg": <number>, "speed_cm_s": <number>}}\n```'
        )
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            prompt, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        # Pure-symbolic: text-only message, no image_url part.
        messages = [{"role": "user",
                     "content": [{"type": "text", "text": prompt}]}]

        # Retry transient VLM failures (the OpenAI-compat aggregator
        # occasionally returns 400 for some model/prompt combinations,
        # especially with image + long structured-state text).  Same
        # input → re-call up to 3 times with exponential backoff.  If
        # all attempts fail we still skip the round (so a chronically-
        # broken model doesn't loop forever) but the agent at least
        # gets several shots at it.
        max_vlm_retries = 3
        last_vlm_err: Optional[Exception] = None
        response: Optional[str] = None
        for attempt in range(max_vlm_retries):
            try:
                response = await loop.run_in_executor(None, vlm.chat, messages)
                last_vlm_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_vlm_err = e
                wait = 2 ** attempt
                log.warning("Round %d VLM attempt %d/%d failed: %s — retry in %ds",
                            round_num, attempt + 1, max_vlm_retries, e, wait)
                await asyncio.sleep(wait)

        if response is None:
            raise RuntimeError("Billiards API request failed; episode is unscored")
        log.info("Round %d: VLM %.1fs", round_num, time.time() - t0)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(
            response, encoding="utf-8")
        save_raw_response(vlm, (workspace / f'round_{round_num:02d}_response.txt').with_suffix(".json"))
        thought = extract_thought(response)
        shot = extract_shot(response)
        (workspace / f"round_{round_num:02d}_thought.txt").write_text(
            thought, encoding="utf-8")
        if shot is None:
            trajectory.append(RoundEntry(round=round_num, thought=thought,
                                         error="no valid ```json shot block"))
            continue
        (workspace / f"round_{round_num:02d}_shot.json").write_text(
            json.dumps(shot, indent=2), encoding="utf-8")

        if log_for_verify.exists(): log_for_verify.unlink()

        verdict: Optional[str] = None
        alive_target = target_total   # pre-parse fallback; correct for any difficulty
        cue_pocketed = False
        new_image: Optional[Path] = None
        round_error: Optional[str] = None
        try:
            runtime_output(await engine.python_exec(assemble_shot(shot, workspace, "image")))

            deadline = time.monotonic() + settle_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if log_for_verify.exists():
                    txt = log_for_verify.read_text(encoding="utf-8")
                    if "# RESULT:" in txt and "SETUP" not in txt:
                        break
            else:
                raise TimeoutError("Billiards verifier did not finish the committed shot")

            # Snap a screenshot of the settled scene.
            await asyncio.sleep(0.5)
            png_path = workspace / f"round_{round_num:02d}.png"
            got = await harvest_screenshot(engine, shot_dir, png_path)
            if got:
                new_image = png_path
        except Exception as e:  # noqa: BLE001
            round_error = f"UE/WS fault: {type(e).__name__}: {e}"
            log.warning("Round %d: %s", round_num, round_error)
            raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e

        verify_log_str = ""
        if log_for_verify.exists():
            verify_log_str = log_for_verify.read_text(encoding="utf-8")
            verdict, alive_target, cue_scratched = parse_verify_log(verify_log_str)
        if verdict not in ("ONGOING", "ALL_DOWN") or not 0 <= alive_target <= target_total:
            raise RuntimeError("Billiards round lacks a valid verifier result")
        (workspace / f"round_{round_num:02d}_verify.txt").write_text(
            verify_log_str or "(no verify log written)", encoding="utf-8")

        if cue_scratched:
            cue_scratch_count += 1
        cumulative_pocketed = target_total - alive_target

        trajectory.append(RoundEntry(
            round=round_num, thought=thought, shot=shot,
            verdict=verdict, alive_target=alive_target,
            cue_pocketed=cue_scratched, error=round_error,
            image=str(new_image) if new_image else None,
        ))
        log.info("Round %d: %s alive=%d/%d cue_scratch=%s (cum_scratches=%d)",
                 round_num, verdict or "TIMEOUT",
                 alive_target, target_total, cue_scratched, cue_scratch_count)

        if new_image is not None:
            last_image_path = new_image
        if verdict == "ALL_DOWN":
            break

    final_result = "PASS" if cumulative_pocketed >= target_total else "FAIL"
    summary = {
        "task": __package__, "model": model_name, "seed": seed,
        "max_rounds": max_rounds, "result": final_result,
        "rounds_used": len(trajectory),
        "final_pocketed": cumulative_pocketed,
        "num_target_balls": target_total,
        "cue_scratch_count": cue_scratch_count,
        "trajectory": [e.__dict__ for e in trajectory],
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s: %s, %d/%d pocketed (%d cue scratches) in %d rounds",
             model_name, final_result, cumulative_pocketed, target_total,
             cue_scratch_count, len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory),
        final_pocketed=cumulative_pocketed,
        cue_pocketed=(cue_scratch_count > 0),
        trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "CONDITION_KEYS"]
