"""Billiards — harness_image_aimline_confirm / shot_code ablation.

Built on top of `harness_image` (vision-only — settled PNG, NO
structured ground truth) **plus** an aim-line preview/confirm REPL:

  Per round
  ─────────
    1. Image-only prompt → model returns proposed `{angle_deg, speed_cm_s}`.
    2. Harness draws a red aim-line beam from the cue ball at that
       angle, takes a screenshot, and shows it back to the model:
       "this is your intended direction — confirm or adjust?"
    3. Model returns either `{confirm: true}` or
       `{adjust: {angle_deg, speed_cm_s}}`.
    4. If adjust: redraw the aim line with the new angle, screenshot,
       ask again. Up to ``max_adjusts_per_round`` adjustment turns.
    5. After confirm OR exhausting the adjust budget, the aim line is
       cleared and the (possibly adjusted) shot fires for real.

Pairs with `harness_aimline_confirm` (the visiongroundtruth-based
counterpart) to measure how much the visual aim-line preview helps
under each information regime:
  - vision-only base  → this harness:           "does aim-line help
                                                 a model that has no
                                                 numeric state?"
  - vision + full GT  → harness_aimline_confirm: "does aim-line add
                                                  anything when the
                                                  model already has
                                                  every coordinate?"
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..._transport import runtime_output, save_raw_response
from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from ..._shared import (
    LABEL_PREFIX,
    assemble_aim_line,
    assemble_clear_aim_line,
    assemble_setup,
    assemble_shot,
    extract_aimline_decision,
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
    "max_rounds": "Number of real shots the agent gets. Default 5.",
    "max_adjusts_per_round": "Aim-line adjust turns per round before forced-fire. Default 3.",
    "settle_timeout": "Seconds to wait for in-scene settle log per real shot. Default 35/50 (auto-bumped for hard).",
    "difficulty": "'standard' (5 balls, 800x800) or 'hard' (10 balls, 1150x800). Default 'standard'.",
}


@dataclass
class AimAdjustment:
    turn: int
    proposed: Optional[dict] = None      # {"angle_deg", "speed_cm_s"} sent into aim-line
    decision: Optional[str] = None       # "confirm" / "adjust" / "parse_fail" / "vlm_failed"
    aim_image: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RoundEntry:
    round: int
    initial_thought: str = ""
    initial_proposal: Optional[dict] = None
    adjustments: List[AimAdjustment] = field(default_factory=list)
    final_shot: Optional[dict] = None
    forced_fire: bool = False            # ran out of adjust budget without confirm
    verdict: Optional[str] = None
    alive_target: int = 0
    cue_pocketed: bool = False
    error: Optional[str] = None
    image: Optional[str] = None          # post-shot png


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


# ---------------------------------------------------------------------------
# VLM call with retry — same pattern as siblings
# ---------------------------------------------------------------------------
async def _vlm_with_retry(loop, vlm, messages, label: str,
                          max_retries: int = 3) -> Optional[str]:
    """VLM call with exponential backoff on either:
      - exceptions (network, 400 from aggregator), OR
      - empty/whitespace response strings (gemini sometimes returns
        empty content with no error when context is large).

    Returns the response text, or ``None`` after exhausting retries.
    """
    last_err: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = await loop.run_in_executor(None, vlm.chat, messages)
            if resp is not None and resp.strip():
                return resp
            last_err = "empty response"
            wait = 2 ** attempt
            log.warning("%s attempt %d/%d returned empty content — retry in %ds",
                        label, attempt + 1, max_retries, wait)
            await asyncio.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            wait = 2 ** attempt
            log.warning("%s attempt %d/%d failed: %s — retry in %ds",
                        label, attempt + 1, max_retries, last_err, wait)
            await asyncio.sleep(wait)
    log.error("%s: all %d VLM attempts failed (last: %s)",
              label, max_retries, last_err)
    raise RuntimeError("Billiards API retries exhausted; episode is unscored")


# GT-block builders are intentionally NOT defined here — this harness
# is image-only.  Pairs with harness_aimline_confirm (which has them)
# to isolate the contribution of structured info under aim-line preview.


async def _draw_aim_line(engine: InteractiveEngine, workspace: Path,
                         angle_deg: float, shot_dir: Path,
                         dest_png: Path) -> Optional[Path]:
    """Spawn aim line at angle_deg, snap a screenshot, return its path."""
    try:
        runtime_output(await engine.python_exec(assemble_aim_line(workspace, angle_deg)))
    except Exception as e:  # noqa: BLE001
        log.warning("aim-line spawn failed: %s", e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e
    await asyncio.sleep(0.5)
    return await harvest_screenshot(engine, shot_dir, dest_png)


async def _clear_aim_line(engine: InteractiveEngine) -> None:
    try:
        runtime_output(await engine.python_exec(assemble_clear_aim_line()))
    except Exception as e:  # noqa: BLE001
        log.warning("aim-line clear failed: %s", e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e


def _strip_old_aim_images(messages: List[dict]) -> int:
    """Drop image_url parts from earlier user turns, keeping only:
      - the FIRST user turn's image (the round's observe screenshot)
      - the LAST user turn's image (the current aim-line preview)

    Each dropped image is replaced with a brief text placeholder so the
    structure is preserved.  Returns the number of images stripped.
    Reason: gemini-3-flash silently returns empty content when the
    prompt accumulates 3+ base64 images, which broke the original
    multi-turn REPL.  Keeping only the LATEST aim-line caps the per-
    call image count at 2 regardless of adjust-turn depth.
    """
    user_idx_with_images = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user" and isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    if len(user_idx_with_images) <= 2:
        return 0
    keep = {user_idx_with_images[0], user_idx_with_images[-1]}
    stripped = 0
    for i in user_idx_with_images:
        if i in keep:
            continue
        m = messages[i]
        new_content = []
        for p in m["content"]:
            if p.get("type") == "image_url":
                new_content.append({
                    "type": "text",
                    "text": "[earlier aim-line image discarded for context budget]",
                })
                stripped += 1
            else:
                new_content.append(p)
        m["content"] = new_content
    return stripped


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    exe: Path,
    max_rounds: int = 5,
    max_adjusts_per_round: int = 3,
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
                       "max_rounds": max_rounds,
                       "max_adjusts_per_round": max_adjusts_per_round,
                       "settle_timeout": settle_timeout,
                       "difficulty": difficulty,
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

    # ── Rounds 1..N ────────────────────────────────────────────────────
    for round_num in range(1, max_rounds + 1):
        log.info("Round %d/%d (cumulative pocketed=%d/%d)",
                 round_num, max_rounds, cumulative_pocketed, target_total)
        round_entry = RoundEntry(round=round_num)

        scoreboard = (
            f"## Scoreboard\n"
            f"Pocketed target balls so far: {cumulative_pocketed}/{target_total}\n"
            f"Cue scratches so far: {cue_scratch_count}"
        )

        # ── Turn 0: initial proposal ──
        initial_text = (
            f"{task_md}\n\n{api_md}\n\n{scoreboard}\n\n"
            f"This is Round {round_num}/{max_rounds}. The attached image "
            f"shows the settled table — vision-only, no numeric ball "
            f"positions or physics constants are provided.\n\n"
            f"# Example actions\n```python\n{example_code}\n```\n\n"
            f"Step 1 of this round: PROPOSE a cue shot. After you reply "
            f"with `{{angle_deg, speed_cm_s}}` we'll draw a red aim line "
            f"on the table at your proposed angle and ask you to confirm "
            f"or adjust (up to {max_adjusts_per_round} adjustments)."
        )
        parts: list = []
        if last_image_path is not None and last_image_path.exists():
            parts.append({"type": "image_url",
                          "image_url": {"url": png_to_base64_url(last_image_path)}})
        parts.append({"type": "text", "text": initial_text})
        messages: List[dict] = [{"role": "user", "content": parts}]

        (workspace / f"round_{round_num:02d}_turn_00_prompt.txt").write_text(
            (f"[image: {last_image_path.name}]\n\n" if last_image_path else "")
            + initial_text, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        response = await _vlm_with_retry(
            loop, vlm, messages, label=f"Round {round_num} initial")
        log.info("Round %d initial: VLM %.1fs", round_num, time.time() - t0)
        if response is None:
            raise RuntimeError("Billiards API request failed; episode is unscored")

        (workspace / f"round_{round_num:02d}_turn_00_response.txt").write_text(
            response, encoding="utf-8")
        save_raw_response(vlm, (workspace / f'round_{round_num:02d}_turn_00_response.txt').with_suffix(".json"))
        round_entry.initial_thought = extract_thought(response)
        proposal = extract_shot(response)
        if proposal is None:
            round_entry.error = "no parseable shot in initial proposal"
            trajectory.append(round_entry)
            continue
        round_entry.initial_proposal = proposal
        messages.append({"role": "assistant", "content": response})
        log.info("Round %d initial proposal: angle=%.1f speed=%.0f",
                 round_num, proposal["angle_deg"], proposal["speed_cm_s"])

        # ── Aim-line REPL: up to N adjust turns ──
        current_proposal = dict(proposal)
        committed = False
        for adjust_turn in range(1, max_adjusts_per_round + 1):
            aim_png = workspace / f"round_{round_num:02d}_aim_{adjust_turn:02d}.png"
            got = await _draw_aim_line(
                engine, workspace, current_proposal["angle_deg"],
                shot_dir, aim_png)
            adj_entry = AimAdjustment(
                turn=adjust_turn, proposed=dict(current_proposal),
                aim_image=str(aim_png) if got else None)

            if got is None:
                adj_entry.error = "aim-line screenshot failed"
                round_entry.adjustments.append(adj_entry)
                committed = True
                break

            remaining = max_adjusts_per_round - adjust_turn
            confirm_prompt = (
                f"## Aim-line preview (Round {round_num}, adjust turn "
                f"{adjust_turn}/{max_adjusts_per_round})\n\n"
                f"Your proposed shot: angle_deg="
                f"{current_proposal['angle_deg']:.2f}, speed_cm_s="
                f"{current_proposal['speed_cm_s']:.0f}\n\n"
                f"The attached image is the settled table with a **red "
                f"beam** emanating from the cue ball at your proposed "
                f"angle. The beam shows direction only; speed is not "
                f"visualised.\n\n"
                f"Reply with EITHER:\n"
                f"  ```json\n  {{\"confirm\": true}}\n  ```\n"
                f"  → fire the proposed shot as-is.\n\n"
                f"  ```json\n  {{\"adjust\": {{\"angle_deg\": <number>, "
                f"\"speed_cm_s\": <number>}}}}\n  ```\n"
                f"  → re-aim with a new angle/speed; you'll see the new "
                f"line and be asked again.\n\n"
                f"You have {remaining} more adjustment(s) after this one. "
                f"At 0 remaining, the next reply MUST be a confirm or the "
                f"current proposal is fired anyway."
            )

            (workspace / f"round_{round_num:02d}_turn_{adjust_turn:02d}_prompt.txt"
             ).write_text(f"[image: {aim_png.name}]\n\n" + confirm_prompt,
                          encoding="utf-8")

            messages.append({"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": png_to_base64_url(aim_png)}},
                {"type": "text", "text": confirm_prompt}]})

            # Cap the per-call image count at 2 (round's observe + the
            # current aim-line). Without this, 3+ base64 images can
            # silently break gemini-3-flash (returns empty content).
            stripped = _strip_old_aim_images(messages)
            if stripped:
                log.debug("Round %d adjust %d: stripped %d earlier aim image(s)",
                          round_num, adjust_turn, stripped)

            t1 = time.time()
            decision_resp = await _vlm_with_retry(
                loop, vlm, messages,
                label=f"Round {round_num} adjust {adjust_turn}")
            log.info("Round %d adjust %d: VLM %.1fs",
                     round_num, adjust_turn, time.time() - t1)

            if decision_resp is None:
                adj_entry.error = "VLM failed on confirm/adjust"
                adj_entry.decision = "vlm_failed"
                round_entry.adjustments.append(adj_entry)
                # Force-fire current proposal. Ratio of forced_fire vs
                # confirmed shots is recorded in the summary so analysis
                # can split "model-endorsed" vs "harness-forced" outcomes.
                round_entry.forced_fire = True
                committed = True
                break

            (workspace / f"round_{round_num:02d}_turn_{adjust_turn:02d}_response.txt"
             ).write_text(decision_resp, encoding="utf-8")
            save_raw_response(vlm, (workspace / f'round_{round_num:02d}_turn_{adjust_turn:02d}_response.txt').with_suffix(".json"))
            messages.append({"role": "assistant", "content": decision_resp})

            decision = extract_aimline_decision(decision_resp)
            if decision is None:
                adj_entry.decision = "parse_fail"
                round_entry.adjustments.append(adj_entry)
                # Same as the vlm_failed path — fire current proposal
                # and flag forced_fire so the row is filterable.
                round_entry.forced_fire = True
                committed = True
                break

            if decision["action"] == "confirm":
                adj_entry.decision = "confirm"
                round_entry.adjustments.append(adj_entry)
                committed = True
                log.info("Round %d: confirmed at adjust turn %d",
                         round_num, adjust_turn)
                break

            # adjust → update proposal and loop
            adj_entry.decision = "adjust"
            round_entry.adjustments.append(adj_entry)
            current_proposal = decision["shot"]
            log.info("Round %d adjust %d → angle=%.1f speed=%.0f",
                     round_num, adjust_turn,
                     current_proposal["angle_deg"], current_proposal["speed_cm_s"])

        if not committed:
            round_entry.forced_fire = True
            log.info("Round %d: adjust budget exhausted, force-firing "
                     "angle=%.1f speed=%.0f",
                     round_num, current_proposal["angle_deg"],
                     current_proposal["speed_cm_s"])

        # Always clear aim line BEFORE the real shot fires so the
        # post-shot screenshot doesn't have a stale beam in it.
        await _clear_aim_line(engine)
        await asyncio.sleep(0.2)

        round_entry.final_shot = current_proposal
        (workspace / f"round_{round_num:02d}_final_shot.json").write_text(
            json.dumps(current_proposal, indent=2), encoding="utf-8")

        # ── Execute the real shot ──
        if log_for_verify.exists(): log_for_verify.unlink()
        verdict: Optional[str] = None
        alive_target = target_total
        cue_scratched = False
        new_image: Optional[Path] = None
        try:
            runtime_output(await engine.python_exec(
                assemble_shot(current_proposal, workspace, "image")))
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
            round_entry.error = f"UE/WS fault: {type(e).__name__}: {e}"
            log.warning("Round %d real shot: %s", round_num, round_entry.error)
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

        round_entry.verdict = verdict
        round_entry.alive_target = alive_target
        round_entry.cue_pocketed = cue_scratched
        round_entry.image = str(new_image) if new_image else None
        trajectory.append(round_entry)

        log.info("Round %d: %s alive=%d/%d cue_scratch=%s adjusts_used=%d forced=%s",
                 round_num, verdict or "TIMEOUT",
                 alive_target, target_total, cue_scratched,
                 len(round_entry.adjustments), round_entry.forced_fire)

        if new_image is not None:
            last_image_path = new_image
        if verdict == "ALL_DOWN":
            break

    final_result = "PASS" if cumulative_pocketed >= target_total else "FAIL"
    summary = {
        "task": __package__, "model": model_name, "seed": seed,
        "max_rounds": max_rounds,
        "max_adjusts_per_round": max_adjusts_per_round,
        "difficulty": difficulty,
        "result": final_result,
        "rounds_used": len(trajectory),
        "final_pocketed": cumulative_pocketed,
        "num_target_balls": target_total,
        "cue_scratch_count": cue_scratch_count,
        "total_adjustments": sum(len(e.adjustments) for e in trajectory),
        "forced_fire_count": sum(1 for e in trajectory if e.forced_fire),
        "trajectory": [
            {
                "round": e.round,
                "initial_thought": e.initial_thought,
                "initial_proposal": e.initial_proposal,
                "adjustments": [
                    {"turn": a.turn, "proposed": a.proposed,
                     "decision": a.decision, "aim_image": a.aim_image,
                     "error": a.error}
                    for a in e.adjustments
                ],
                "final_shot": e.final_shot,
                "forced_fire": e.forced_fire,
                "verdict": e.verdict,
                "alive_target": e.alive_target,
                "cue_pocketed": e.cue_pocketed,
                "error": e.error,
                "image": e.image,
            }
            for e in trajectory
        ],
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s: %s, %d/%d pocketed (%d cue scratches, %d adjusts, %d forced) in %d rounds",
             model_name, final_result, cumulative_pocketed, target_total,
             cue_scratch_count, summary["total_adjustments"],
             summary["forced_fire_count"], len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory),
        final_pocketed=cumulative_pocketed,
        cue_pocketed=(cue_scratch_count > 0),
        trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "AimAdjustment", "CONDITION_KEYS"]
