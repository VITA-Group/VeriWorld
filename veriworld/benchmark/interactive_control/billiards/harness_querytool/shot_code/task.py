"""Billiards — harness_querytool / shot_code ablation.

Same scene + same prompt content as ``harness_visiongroundtruth``,
**plus** a per-round REPL that gives the agent a ``simulate_shot``
tool.  A simulate runs the same Chaos physics that the real shot
would use, then rolls back ball positions — the agent gets the
deterministic outcome (pocketed list, cue final position, first
contact, event log) without spending real game state.

Within each round the agent may issue up to ``max_simulates_per_round``
queries before it must commit a real ``shoot``.  When the budget hits
zero, the harness rejects further queries and forces a shoot.

Counterpart to:
  * ``harness_visiongroundtruth`` — same info, no simulate tool
    (the strict baseline this harness adds capability to).
  * ``harness_visionposition_doublecall`` — also multi-call but
    the second call is "compute on the numbers", not "probe the
    physics"; that ablation lost to single-call siblings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..._transport import runtime_output, save_raw_response, require_state
from veriworld.common import RunLogger, VLMClient, png_to_base64_url
from veriworld.infra.interactive.engine import InteractiveEngine

from ..._shared import (
    LABEL_PREFIX,
    assemble_setup,
    assemble_shot,
    assemble_simulate,
    extract_repl_action,
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
    "max_simulates_per_round": "Hypothetical simulate_shot queries per round before forced shoot. Default 3.",
    "settle_timeout": "Seconds to wait for in-scene settle log per real shot. Default 35.",
    "simulate_timeout": "Seconds to wait for simulate_result.json per simulate query. Default 35.",
}


@dataclass
class SimulateProbe:
    turn: int
    args: dict
    result: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class RoundEntry:
    round: int
    simulates: List[SimulateProbe] = field(default_factory=list)
    final_thought: str = ""
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


# ---------------------------------------------------------------------------
# VLM call with retry — shared with siblings.  Doubly important here because
# call count per round is up to 1 + max_simulates_per_round, so transient
# 400s would compound.
# ---------------------------------------------------------------------------
async def _vlm_with_retry(loop, vlm, messages, label: str,
                          max_retries: int = 3) -> Optional[str]:
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return await loop.run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 ** attempt
            log.warning("%s attempt %d/%d failed: %s — retry in %ds",
                        label, attempt + 1, max_retries, e, wait)
            await asyncio.sleep(wait)
    log.error("%s: all %d VLM attempts failed (last: %s)",
              label, max_retries, last_err)
    raise RuntimeError("Billiards API retries exhausted; episode is unscored")


def _build_static_block(params: dict) -> str:
    cx, cy = params["table_center_xy"]
    hx = params["table_size_x"] / 2.0
    hy = params["table_size_y"] / 2.0
    gap = params["hole_corner_gap"]
    pockets = {
        "NE_pocket": [cx + hx - gap * 0.25, cy + hy - gap * 0.25],
        "NW_pocket": [cx - hx + gap * 0.25, cy + hy - gap * 0.25],
        "SE_pocket": [cx + hx - gap * 0.25, cy - hy + gap * 0.25],
        "SW_pocket": [cx - hx + gap * 0.25, cy - hy + gap * 0.25],
    }
    block = (
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
        block += f"    {name}: x={p[0]:.0f}, y={p[1]:.0f}\n"
    return block


def _build_dynamic_block(workspace: Path) -> str:
    block = "## Live ball state (ground truth this round, world cm)\n"
    state_path = workspace / "state.json"
    require_state(state_path)
    if not state_path.exists():
        return block + "  (no state.json yet — first round)\n"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        return block + f"  (state.json read failed: {e})\n"
    balls = state.get("balls", {})
    if balls:
        for bid in sorted(balls.keys()):
            x, y, z = balls[bid]
            block += f"  {bid}: x={x}, y={y}, z={z}\n"
    else:
        block += "  (no live balls remain)\n"
    block += (
        f"  alive_target = {state.get('alive_target', '?')}\n"
        f"  cue_scratched_last_round = "
        f"{'yes' if state.get('cue_scratched') else 'no'}\n"
    )
    return block


async def _run_simulate(engine: InteractiveEngine, workspace: Path,
                        sim_args: dict, sim_out: Path,
                        timeout: float) -> Dict[str, Any]:
    """Dispatch a simulate to UE and poll for the result file.  Returns
    the parsed result dict on success, or ``{"ok": False, "error": ...}``
    on timeout / parse failure."""
    if sim_out.exists():
        try: sim_out.unlink()
        except Exception: pass
    try:
        runtime_output(await engine.python_exec(
            assemble_simulate(sim_args, workspace, sim_out)))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"UE exec failed: {type(e).__name__}: {e}"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        if sim_out.exists():
            try:
                txt = sim_out.read_text(encoding="utf-8")
                if not txt.strip(): continue
                return json.loads(txt)
            except Exception:
                continue
    return {"ok": False, "error": f"simulate_result.json not written within {timeout}s"}


def _format_simulate_result(r: dict) -> str:
    """Render a simulate result as a compact agent-facing block."""
    if not r.get("ok"):
        return f"```json\n{json.dumps({'ok': False, 'error': r.get('error', 'unknown')}, indent=2)}\n```"
    summary = {
        "ok": True,
        "shot": r.get("shot"),
        "duration_s": r.get("duration_s"),
        "pocketed_balls": r.get("pocketed_balls", []),
        "cue_pocketed": r.get("cue_pocketed", False),
        "first_cue_contact": r.get("first_cue_contact"),
        "ball_final_pos": r.get("ball_final_pos", {}),
        "events": r.get("events", []),
    }
    return f"```json\n{json.dumps(summary, indent=2)}\n```"


async def run(
    engine: InteractiveEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    *,
    exe: Path,
    max_rounds: int = 5,
    max_simulates_per_round: int = 3,
    settle_timeout: float = 35.0,
    simulate_timeout: float = 35.0,
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
                       "max_rounds": max_rounds,
                       "max_simulates_per_round": max_simulates_per_round,
                       "settle_timeout": settle_timeout,
                       "simulate_timeout": simulate_timeout,
                       "exe": str(exe)},
    )

    task_md = (TASK_ROOT / "task.md").read_text(encoding="utf-8")
    api_md = (HARNESS_DIR / "api.md").read_text(encoding="utf-8")
    example_code = (HARNESS_DIR / "example.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    sim_out = workspace / "simulate_result.json"   # reused per query
    shot_dir = screenshot_dir_from_exe(exe)
    trajectory: List[RoundEntry] = []
    last_image_path: Optional[Path] = None
    cumulative_pocketed = 0
    cue_scratch_count = 0
    static_block = _build_static_block(params)

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
            f"Cue scratches so far: {cue_scratch_count} "
            f"(scratching does not end the episode — the cue is teleported "
            f"back to the table centre and you fire from there next round)"
        )
        dynamic_block = _build_dynamic_block(workspace)

        remaining = max_simulates_per_round
        initial_text = (
            f"{task_md}\n\n{api_md}\n\n{scoreboard}\n\n"
            f"{static_block}\n{dynamic_block}\n"
            f"This is Round {round_num}/{max_rounds}. The attached image shows "
            f"the settled table; the listings above give every quantitative "
            f"parameter and the exact (x, y, z) of each alive ball.\n\n"
            f"You have **{remaining} simulate_shot** "
            f"queries available before you must commit a real shoot. "
            f"Use them to probe candidate shots — the simulator runs the "
            f"same Chaos physics and rolls back state when done.\n\n"
            f"# Example actions\n```python\n{example_code}\n```\n\n"
            f"Reply with EITHER a query OR a shoot, in the format described above."
        )

        # Initial user message (image + scoreboard + tool desc).
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
        committed_shot: Optional[dict] = None
        committed_thought: str = ""
        round_giveup_reason: Optional[str] = None
        turn = 0
        max_turns = max_simulates_per_round + 1   # K queries + 1 shoot

        while turn < max_turns:
            t0 = time.time()
            response = await _vlm_with_retry(
                loop, vlm, messages,
                label=f"Round {round_num} turn {turn}")
            log.info("Round %d turn %d: VLM %.1fs",
                     round_num, turn, time.time() - t0)
            if response is None:
                raise RuntimeError("Billiards API request failed; episode is unscored")

            (workspace / f"round_{round_num:02d}_turn_{turn:02d}_response.txt"
             ).write_text(response, encoding="utf-8")
            save_raw_response(vlm, (workspace / f'round_{round_num:02d}_turn_{turn:02d}_response.txt').with_suffix(".json"))

            action = extract_repl_action(response)
            if action is None:
                # Tell the model its output didn't parse and let it retry once.
                err_msg = (
                    "Your last reply did not contain a parsable JSON action. "
                    "Reply with one of the two action shapes documented in api.md "
                    "(query or shoot)."
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": err_msg})
                turn += 1
                next_prompt_path = (
                    workspace / f"round_{round_num:02d}_turn_{turn:02d}_prompt.txt")
                next_prompt_path.write_text(err_msg, encoding="utf-8")
                continue

            if action["action"] == "shoot":
                committed_thought = extract_thought(response)
                committed_shot = action["shot"]
                messages.append({"role": "assistant", "content": response})
                break

            # action == "query"
            if remaining <= 0:
                # Shouldn't normally happen — prompt told the agent it was
                # out — but guard anyway.  Tell agent and force-shoot next.
                messages.append({"role": "assistant", "content": response})
                err_msg = (
                    "Your simulate budget is exhausted. You MUST reply with "
                    "a shoot action this turn."
                )
                messages.append({"role": "user", "content": err_msg})
                turn += 1
                continue

            sim_args = action["args"]
            probe = SimulateProbe(turn=turn, args=sim_args)
            sim_result = await _run_simulate(
                engine, workspace, sim_args, sim_out,
                timeout=simulate_timeout)
            if not sim_result.get("ok"):
                probe.error = sim_result.get("error", "unknown")
                log.warning("Round %d simulate turn %d failed: %s",
                            round_num, turn, probe.error)
            else:
                probe.result = sim_result
                log.info("Round %d simulate turn %d: pocketed=%s cue_pocketed=%s",
                         round_num, turn,
                         sim_result.get("pocketed_balls"),
                         sim_result.get("cue_pocketed"))
            round_entry.simulates.append(probe)

            remaining -= 1
            sim_block = _format_simulate_result(sim_result)
            next_user = (
                f"simulate_shot result:\n{sim_block}\n\n"
                f"remaining_simulates: {remaining}\n\n"
                + ("You MUST issue a shoot action next."
                   if remaining == 0
                   else "Reply with another query or commit with shoot.")
            )
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": next_user})
            turn += 1
            (workspace / f"round_{round_num:02d}_turn_{turn:02d}_prompt.txt"
             ).write_text(next_user, encoding="utf-8")

        # Persist this round's simulate log.
        (workspace / f"round_{round_num:02d}_simulates.json").write_text(
            json.dumps([{"turn": p.turn, "args": p.args,
                         "result": p.result, "error": p.error}
                        for p in round_entry.simulates],
                       indent=2), encoding="utf-8")

        if committed_shot is None:
            round_entry.error = (
                round_giveup_reason or
                f"no shoot committed within {max_turns} turns")
            log.warning("Round %d: %s", round_num, round_entry.error)
            trajectory.append(round_entry)
            continue

        round_entry.final_thought = committed_thought
        round_entry.shot = committed_shot
        (workspace / f"round_{round_num:02d}_thought.txt").write_text(
            committed_thought, encoding="utf-8")
        (workspace / f"round_{round_num:02d}_shot.json").write_text(
            json.dumps(committed_shot, indent=2), encoding="utf-8")

        # ── Execute the real shot ──
        if log_for_verify.exists(): log_for_verify.unlink()
        verdict: Optional[str] = None
        alive_target = 5
        cue_scratched = False
        new_image: Optional[Path] = None
        try:
            runtime_output(await engine.python_exec(
                assemble_shot(committed_shot, workspace, "image")))
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

        log.info("Round %d: %s alive=%d/%d cue_scratch=%s sims_used=%d",
                 round_num, verdict or "TIMEOUT",
                 alive_target, target_total, cue_scratched,
                 len(round_entry.simulates))

        if new_image is not None:
            last_image_path = new_image
        if verdict == "ALL_DOWN":
            break

    final_result = "PASS" if cumulative_pocketed >= target_total else "FAIL"
    summary = {
        "task": __package__, "model": model_name, "seed": seed,
        "max_rounds": max_rounds,
        "max_simulates_per_round": max_simulates_per_round,
        "result": final_result,
        "rounds_used": len(trajectory),
        "final_pocketed": cumulative_pocketed,
        "num_target_balls": target_total,
        "cue_scratch_count": cue_scratch_count,
        "total_simulates": sum(len(e.simulates) for e in trajectory),
        "trajectory": [
            {
                "round": e.round,
                "simulates": [{"turn": p.turn, "args": p.args,
                               "result": p.result, "error": p.error}
                              for p in e.simulates],
                "simulates_used": len(e.simulates),
                "final_thought": e.final_thought,
                "shot": e.shot,
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
    log.info("%s: %s, %d/%d pocketed (%d cue scratches, %d sims) in %d rounds",
             model_name, final_result, cumulative_pocketed, target_total,
             cue_scratch_count, summary["total_simulates"], len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory),
        final_pocketed=cumulative_pocketed,
        cue_pocketed=(cue_scratch_count > 0),
        trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "SimulateProbe", "CONDITION_KEYS"]
