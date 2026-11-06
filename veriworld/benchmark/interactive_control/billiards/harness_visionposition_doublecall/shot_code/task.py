"""Billiards — harness_visionposition_doublecall / shot_code ablation.

Each round splits into TWO sequential VLM calls (same scene state
between them — no shot is applied until Call 2 returns):

  Call 1 (vision-only analysis)
      Input:  screenshot of the settled table + scoreboard +
              the analysis instructions.  No coordinates given.
      Asked for:  a strategic plan (target ball, target pocket,
              intent) PLUS a list of ball IDs whose exact (x, y, z)
              the agent needs to compute the shot.

  Call 2 (text-only, with queried coordinates injected)
      Input:  the queried balls' (x, y, z) read from the current
              state.json + the agent's own Call-1 plan text.
              No image (force the agent to commit to its earlier
              visual analysis and reason numerically).
      Asked for:  the final {"angle_deg", "speed_cm_s"} shot.

Compared to ``harness_visionposition`` (single call, agent's query
results only arrive in NEXT round's prompt) and
``harness_visiongroundtruth`` (single call, every coordinate
pushed every round), this harness:

  * Forces a strategy-first / arithmetic-second decomposition.
  * Avoids the visionposition latency: queries are answered
    in-round.
  * Hides the image during the numerical step so the model can't
    silently re-do vision when given the chance.

This isolates the question: is the gain of structured ground
truth coming from "I have the numbers" (visiongroundtruth) or
from "I asked for the right numbers and reasoned about them"
(this harness)?
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from ..._transport import runtime_output, save_raw_response, require_state
from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from ..._shared import (
    LABEL_PREFIX,
    assemble_setup,
    assemble_shot,
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
    "settle_timeout": "Seconds to wait for in-scene settle log per round. Default 35.",
}


_CODE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def extract_call1_plan(response: str):
    """Parse Call-1 response.  Expected JSON shape::

        {"intent": "<short plain-text plan>",
         "query_balls": ["cue", "ball_2", ...]}

    Returns ``(intent_text, list_of_ball_ids)``.  Both default to
    empty / [] on parse failure (caller must handle).
    """
    m = _CODE_RE.search(response)
    if not m:
        return "", []
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return "", []
    if not isinstance(obj, dict):
        return "", []
    intent = str(obj.get("intent", "")).strip()
    queries = obj.get("query_balls", [])
    if not isinstance(queries, list):
        queries = []
    queries = [str(q) for q in queries if isinstance(q, (str, int))]
    return intent, queries


def extract_call2_shot(response: str):
    """Parse Call-2 response.  Expected JSON shape::

        {"shot": {"angle_deg": <n>, "speed_cm_s": <n>}}

    Or flat ``{"angle_deg": ..., "speed_cm_s": ...}``.  Returns
    a shot dict or ``None``.
    """
    m = _CODE_RE.search(response)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    src = obj["shot"] if "shot" in obj and isinstance(obj["shot"], dict) else obj
    try:
        return {"angle_deg": float(src.get("angle_deg", 0.0)),
                "speed_cm_s": float(src.get("speed_cm_s", 0.0))}
    except (TypeError, ValueError):
        return None


@dataclass
class RoundEntry:
    round: int
    call1_thought: str = ""
    call1_intent: str = ""
    call1_queries: List[str] = field(default_factory=list)
    queried_positions: dict = field(default_factory=dict)
    call2_thought: str = ""
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


async def _vlm_with_retry(loop, vlm, messages, round_num: int, label: str,
                          max_retries: int = 3):
    """Same retry pattern as the sibling harnesses — protects against
    the OpenAI-compat aggregator's intermittent 400s on long
    image+text payloads."""
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return await loop.run_in_executor(None, vlm.chat, messages), None
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt
            log.warning("Round %d %s: attempt %d/%d failed: %s — retry in %ds",
                        round_num, label, attempt + 1, max_retries, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("Billiards API retries exhausted; episode is unscored")


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    exe: Path,
    max_rounds: int = 5,
    settle_timeout: float = 35.0,
    **_unused: Any,
) -> EpisodeResult:
    from ...generate_params import generate as generate_params
    params = generate_params(seed=seed)
    target_total = params["num_target_balls"]

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
        await asyncio.sleep(2)
        observe_png = workspace / "round_00_observe.png"
        got = await harvest_screenshot(engine, shot_dir, observe_png)
        if got:
            last_image_path = observe_png
    except Exception as e:  # noqa: BLE001
        log.warning("Round 0 fault: %s: %s", type(e).__name__, e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e

    # ── Rounds 1..N (each is two sequential VLM calls) ────────────────
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

        # ── Call 1: vision-only analysis + query selection ──────────
        call1_prompt = (
            f"{task_md}\n\n{api_md}\n\n{scoreboard}\n\n"
            f"This is Round {round_num}/{max_rounds} — Call 1 of 2 "
            f"(vision-only strategy step).\n\n"
            f"You see the settled table from the previous round (or the "
            f"initial rack on Round 1). Decide which coloured ball you'll "
            f"attack and into which pocket; describe the intent in one to "
            f"three sentences. Then list the ball IDs whose exact "
            f"coordinates you need for the next call to compute the shot.\n\n"
            f"# Example shot (illustrative — tune)\n```python\n{example_code}\n```\n\n"
            f"Reply with:\n"
            f"thought: <observations from the image>\n\n"
            f'```json\n{{\n'
            f'  "intent": "<short plan, e.g. \\"pocket yellow into NW pocket on a thin cut\\">",\n'
            f'  "query_balls": ["cue", "ball_2"]\n'
            f'}}\n```'
        )
        (workspace / f"round_{round_num:02d}_call1_prompt.txt").write_text(
            (f"[image: {last_image_path.name}]\n\n" if last_image_path else "")
            + call1_prompt, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        parts: list = []
        if last_image_path is not None and last_image_path.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(last_image_path)}})
        parts.append({"type": "text", "text": call1_prompt})
        messages = [{"role": "user", "content": parts}]

        call1_response, err = await _vlm_with_retry(loop, vlm, messages, round_num, "call1")
        if call1_response is None:
            log.error("Round %d Call 1 failed after retries; skipping round", round_num)
            trajectory.append(RoundEntry(round=round_num,
                                         error=f"Call 1 failed: {err}"))
            continue
        log.info("Round %d Call 1: VLM %.1fs", round_num, time.time() - t0)
        (workspace / f"round_{round_num:02d}_call1_response.txt").write_text(
            call1_response, encoding="utf-8")
        save_raw_response(vlm, (workspace / f'round_{round_num:02d}_call1_response.txt').with_suffix(".json"))

        call1_thought = extract_thought(call1_response)
        intent, query_balls = extract_call1_plan(call1_response)

        # ── Pull queried positions from the CURRENT state.json ──────
        queried_positions: dict = {}
        state_path = workspace / "state.json"
        require_state(state_path)
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                balls = state.get("balls", {})
                for bid in query_balls:
                    if bid in balls:
                        queried_positions[bid] = balls[bid]
            except Exception as e:  # noqa: BLE001
                log.warning("Round %d: state.json read for query failed: %s",
                            round_num, e)

        positions_block = "## Queried positions (ground truth, world cm)\n"
        if queried_positions:
            for bid in query_balls:
                if bid in queried_positions:
                    x, y, z = queried_positions[bid]
                    positions_block += f"  {bid}: x={x}, y={y}, z={z}\n"
                else:
                    positions_block += f"  {bid}: (not on table — pocketed or unknown id)\n"
        elif query_balls:
            positions_block += "  (no positions returned — state.json missing)\n"
        else:
            positions_block += "  (you didn't request any ball positions in Call 1)\n"

        (workspace / f"round_{round_num:02d}_query_results.json").write_text(
            json.dumps({"query_balls": query_balls,
                        "queried_positions": queried_positions}, indent=2),
            encoding="utf-8")

        # Static physics block — small, useful for arithmetic.
        cx, cy = params["table_center_xy"]
        hx = params["table_size_x"] / 2.0
        hy = params["table_size_y"] / 2.0
        gap = params["hole_corner_gap"]
        pockets = {
            "NE": [cx + hx - gap * 0.25, cy + hy - gap * 0.25],
            "NW": [cx - hx + gap * 0.25, cy + hy - gap * 0.25],
            "SE": [cx + hx - gap * 0.25, cy - hy + gap * 0.25],
            "SW": [cx - hx + gap * 0.25, cy - hy + gap * 0.25],
        }
        static_block = (
            "## Static physics (world cm)\n"
            f"  ball_radius      = {params['ball_radius']}\n"
            f"  cue_max_speed    = {params['cue_max_speed_cm_s']} cm/s\n"
            f"  cue_lin_damping  = {params['cue_linear_damping']}\n"
            f"  ball_lin_damping = {params['ball_linear_damping']}\n"
            f"  pocket_centres   ="
        )
        for name, p in pockets.items():
            static_block += f"\n    {name}: x={p[0]:.0f}, y={p[1]:.0f}"
        static_block += "\n"

        # ── Call 2: text-only computation ───────────────────────────
        call2_prompt = (
            f"{api_md}\n\n{scoreboard}\n\n"
            f"This is Round {round_num}/{max_rounds} — Call 2 of 2 "
            f"(text-only computation step). NO image is attached on "
            f"purpose: commit to the strategy you formed in Call 1 and "
            f"compute the shot numerically.\n\n"
            f"## Your Call-1 intent\n{intent or '(empty — agent did not declare an intent)'}\n\n"
            f"{positions_block}\n"
            f"{static_block}\n"
            f"Compute the cue shot ({{angle_deg, speed_cm_s}}) that "
            f"executes your intent.  Use the queried positions as "
            f"ground truth; reason about ghost-ball geometry and "
            f"required cue speed.\n\n"
            f"Reply with:\n"
            f"thought: <numeric reasoning — show the geometry>\n\n"
            f'```json\n{{"shot": {{"angle_deg": <number>, "speed_cm_s": <number>}}}}\n```'
        )
        (workspace / f"round_{round_num:02d}_call2_prompt.txt").write_text(
            call2_prompt, encoding="utf-8")

        t1 = time.time()
        messages2 = [{"role": "user", "content": [
            {"type": "text", "text": call2_prompt}]}]
        call2_response, err = await _vlm_with_retry(loop, vlm, messages2, round_num, "call2")
        if call2_response is None:
            log.error("Round %d Call 2 failed after retries; skipping round", round_num)
            trajectory.append(RoundEntry(
                round=round_num, call1_thought=call1_thought,
                call1_intent=intent, call1_queries=query_balls,
                queried_positions=queried_positions,
                error=f"Call 2 failed: {err}"))
            continue
        log.info("Round %d Call 2: VLM %.1fs", round_num, time.time() - t1)
        (workspace / f"round_{round_num:02d}_call2_response.txt").write_text(
            call2_response, encoding="utf-8")
        save_raw_response(vlm, (workspace / f'round_{round_num:02d}_call2_response.txt').with_suffix(".json"))

        call2_thought = extract_thought(call2_response)
        shot = extract_call2_shot(call2_response)
        if shot is None:
            trajectory.append(RoundEntry(
                round=round_num, call1_thought=call1_thought,
                call1_intent=intent, call1_queries=query_balls,
                queried_positions=queried_positions,
                call2_thought=call2_thought,
                error="no valid {\"shot\":{...}} JSON in Call 2"))
            continue
        (workspace / f"round_{round_num:02d}_shot.json").write_text(
            json.dumps(shot, indent=2), encoding="utf-8")

        # ── Apply shot via UE, settle, harvest ──────────────────────
        if log_for_verify.exists(): log_for_verify.unlink()
        verdict: Optional[str] = None
        alive_target = 5
        cue_scratched = False
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
            round=round_num,
            call1_thought=call1_thought, call1_intent=intent,
            call1_queries=query_balls, queried_positions=queried_positions,
            call2_thought=call2_thought, shot=shot,
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
