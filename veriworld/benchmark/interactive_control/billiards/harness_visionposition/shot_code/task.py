"""Billiards — harness_visionposition / shot_code ablation.

Same scene + same shot logic as harness_image. The agent's action
JSON is wrapped: it now contains BOTH a ``shot`` and an optional
``query_balls`` list — a list of ball IDs the agent wants positions
for. The harness reads those balls' (x, y, z) from ``state.json``
after the round settles and feeds the result back in the **next**
round's prompt under a ``## Your last query_balls results`` block.

This is the **active-query** counterfactual:

  * ``harness_image``                — vision only (no positions ever)
  * ``harness_visionposition``       — agent must explicitly request
                                       positions; "queries" cost nothing
                                       but the agent has to think to ask
  * ``harness_visiongroundtruth``    — every position pushed every round
                                       without asking
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
from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

import re

from ..._shared import (
    LABEL_PREFIX,
    assemble_setup,
    assemble_shot,
    extract_thought,
    parse_verify_log,
)
from .._common import harvest_screenshot, screenshot_dir_from_exe


_CODE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def extract_shot_and_queries(response: str):
    """Parse the wrapped JSON action block.

    Expected shape::

        {"shot": {"angle_deg": <n>, "speed_cm_s": <n>},
         "query_balls": ["cue", "ball_2", ...]}     # optional, omittable

    Backward compat: also accepts the flat
    ``{"angle_deg":..,"speed_cm_s":..}`` form (treated as no query).

    Returns ``(shot_dict_or_None, list_of_ball_ids)``.
    """
    m = _CODE_RE.search(response)
    if not m:
        return None, []
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return None, []
    if not isinstance(obj, dict):
        return None, []
    queries = obj.get("query_balls", [])
    if not isinstance(queries, list):
        queries = []
    queries = [str(q) for q in queries if isinstance(q, (str, int))]
    if "shot" in obj and isinstance(obj["shot"], dict):
        s = obj["shot"]
    else:
        s = obj
    try:
        shot = {"angle_deg": float(s.get("angle_deg", 0.0)),
                "speed_cm_s": float(s.get("speed_cm_s", 0.0))}
    except (TypeError, ValueError):
        return None, queries
    return shot, queries

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
HARNESS_DIR = HERE.parent
TASK_ROOT = HARNESS_DIR.parent

CONDITION_KEYS = {
    "max_rounds": "Number of shots the agent gets. Default 5.",
    "settle_timeout": "Seconds to wait for in-scene settle log per round. Default 35/50 (auto-bumped for hard).",
    "difficulty": "'standard' (5 balls, 800x800) or 'hard' (10 balls, 1150x800). Default 'standard'.",
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
    last_query_block: str = ""   # populated AFTER each shot from agent's query_balls

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

        prompt = (
            f"{task_md}\n\n{api_md}\n\n{scoreboard}\n\n"
            + (last_query_block + "\n" if last_query_block else "")
            + f"This is Round {round_num}/{max_rounds}. The attached image "
              f"shows the settled table. To get exact ball coordinates next "
              f"round, include a `query_balls` list in your action JSON.\n\n"
            f"# Example action (illustrative — tune)\n```python\n{example_code}\n```\n\n"
            f"Reply with:\n"
            f"thought: <describe positions, intent, and which balls (if any) you want exact coords for next round>\n\n"
            f'```json\n{{\n'
            f'  "shot": {{"angle_deg": <number>, "speed_cm_s": <number>}},\n'
            f'  "query_balls": ["cue", "ball_0", ...]   // optional; empty/omitted = no query\n'
            f'}}\n```'
        )
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            (f"[image: {last_image_path.name}]\n\n" if last_image_path else "")
            + prompt, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        parts: list = []
        if last_image_path is not None and last_image_path.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(last_image_path)}})
        parts.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": parts}]

        # Retry transient VLM failures with same input (the OpenAI-compat
        # aggregator occasionally 400s on long image+text payloads).
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
        shot, query_balls = extract_shot_and_queries(response)
        (workspace / f"round_{round_num:02d}_thought.txt").write_text(
            thought, encoding="utf-8")
        if shot is None:
            trajectory.append(RoundEntry(round=round_num, thought=thought,
                                         error="no valid ```json shot block"))
            continue
        (workspace / f"round_{round_num:02d}_shot.json").write_text(
            json.dumps({"shot": shot, "query_balls": query_balls},
                       indent=2),
            encoding="utf-8")

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

        # Build the query-results block for the NEXT round's prompt.
        # Reads the scene-written state.json (always present after harvest).
        if query_balls:
            lines = [f"## Your last `query_balls` request returned"]
            state_file = workspace / "state.json"
            require_state(state_file)
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    balls = state.get("balls", {})
                    for bid in query_balls:
                        if bid in balls:
                            x, y, z = balls[bid]
                            lines.append(f"  {bid}: x={x}, y={y}, z={z}")
                        else:
                            lines.append(f"  {bid}: (not on table — pocketed or unknown id)")
                except Exception as e:
                    lines.append(f"  (state.json read failed: {e})")
            else:
                lines.append("  (no state.json available)")
            last_query_block = "\n".join(lines)
        else:
            last_query_block = ""

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
