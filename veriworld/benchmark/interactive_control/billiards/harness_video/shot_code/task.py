"""Billiards — harness_video / shot_code ablation.

InteractiveEngine: ONE UE instance per seed, shared across every
round. Round 0 spawns the table+balls and records a still observation
mp4; Rounds 1..N apply the agent's cue shot, record the physics
playback, and feed the mp4 back into the next prompt via
``vlm.chat_with_video``.

Why InteractiveEngine: balls never re-spawn — end-of-round-N positions
ARE start-of-round-(N+1) positions automatically (the physics body
isn't touched between rounds), so what the agent sees in the prior
round's video matches what its next shot acts on. The previous
ComputationalEngine attempt round-tripped state via state.json and
was vulnerable to spawn-time physics drift.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from ..._transport import runtime_output, save_raw_response
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
from .._common import harvest_video, stop_recording

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
HARNESS_DIR = HERE.parent
TASK_ROOT = HARNESS_DIR.parent

CONDITION_KEYS = {
    "max_rounds": "Number of shots the agent gets. Default 5.",
    "settle_timeout": "Seconds to wait for in-scene settle log per round. Default 35 (covers EXTENDED_CAP_S=25 + harvest write latency).",
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
    video: Optional[str] = None


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
        task_module=__package__,
        model_name=model_name,
        seed=seed,
        resolved_args={"seed": seed, "model": model_name,
                       "max_rounds": max_rounds, "settle_timeout": settle_timeout,
                       "exe": str(exe)},
    )

    task_md = (TASK_ROOT / "task.md").read_text(encoding="utf-8")
    api_md = (HARNESS_DIR / "api.md").read_text(encoding="utf-8")
    example_code = (HARNESS_DIR / "example.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    trajectory: List[RoundEntry] = []
    last_video_path: Optional[Path] = None
    cumulative_pocketed = 0
    cue_scratch_count = 0

    # Switch to Untitled level (chaos physics requires it).
    try:
        await engine.switch_level("/Game/Levels/Untitled")
    except Exception as e:  # noqa: BLE001
        log.warning("level switch failed (continuing): %s", e)
        raise RuntimeError("Billiards runtime/capture failed; episode is unscored") from e
    await asyncio.sleep(2)

    # ── Round 0: build scene, record observation ──────────────────────
    log.info("Round 0: scene setup + observation recording")
    if log_for_verify.exists(): log_for_verify.unlink()
    try:
        runtime_output(await engine.python_exec(assemble_setup(workspace)))
        await asyncio.sleep(1)

        # Quick observation record — table at rest, no shot.
        t_rec = time.time()
        runtime_output(await engine.python_exec(
            "import unreal_runtime as ur\n"
            "ur.StartRecording(width=640, height=480, codec='h264')"))
        await asyncio.sleep(3)
        recording = await stop_recording(engine)
        await asyncio.sleep(3)

        observe_mp4 = workspace / "round_00_observe.mp4"
        if harvest_video(recording, observe_mp4):
            last_video_path = observe_mp4
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
            f"This is Round {round_num}/{max_rounds}. The attached video shows "
            f"the table at the end of the previous round (or the initial rack on "
            f"Round 1). Read positions, choose a shot.\n\n"
            f"# Example shot (illustrative — tune)\n```python\n{example_code}\n```\n\n"
            f"Reply with:\n"
            f"thought: <describe positions and intent>\n\n"
            f'```json\n{{"angle_deg": <number>, "speed_cm_s": <number>}}\n```'
        )
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            (f"[video: {last_video_path.name}]\n\n" if last_video_path else "")
            + prompt, encoding="utf-8")

        # Policy call (with retry on transient failures).  See sibling
        # harnesses for the rationale: model-comparison fairness
        # requires that one model's intermittent 400s/timeouts don't
        # silently consume rounds another model would have used.
        loop = asyncio.get_event_loop()
        t0 = time.time()

        async def _one_call():
            if last_video_path is None or not last_video_path.is_file():
                raise RuntimeError("Billiards video observation is missing")
            return await loop.run_in_executor(None, vlm.chat_with_video, prompt, last_video_path)

        max_vlm_retries = 3
        last_vlm_err: Optional[Exception] = None
        response: Optional[str] = None
        for attempt in range(max_vlm_retries):
            try:
                response = await _one_call()
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

        (workspace / "recording_current.json").unlink(missing_ok=True)

        # Apply shot via persistent UE.
        verdict: Optional[str] = None
        alive_target = 5
        cue_scratched = False
        new_video: Optional[Path] = None
        round_error: Optional[str] = None
        try:
            t_rec = time.time()
            runtime_output(await engine.python_exec(assemble_shot(shot, workspace, "video")))

            deadline = time.monotonic() + settle_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if log_for_verify.exists():
                    txt = log_for_verify.read_text(encoding="utf-8")
                    if "# RESULT:" in txt and "SETUP" not in txt:
                        break
            else:
                raise TimeoutError("Billiards verifier did not finish the committed shot")
            await asyncio.sleep(2)

            video_path = workspace / f"round_{round_num:02d}_video.mp4"
            recording = json.loads((workspace / "recording_current.json").read_text(encoding="utf-8"))
            got = harvest_video(recording, video_path)
            if got:
                new_video = video_path
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
            video=str(new_video) if new_video else None,
        ))
        log.info("Round %d: %s alive=%d/%d cue_scratch=%s (cum_scratches=%d)",
                 round_num, verdict or "TIMEOUT",
                 alive_target, target_total, cue_scratched, cue_scratch_count)

        if new_video is not None:
            last_video_path = new_video

        if verdict == "ALL_DOWN":
            break

    # ── Summary ────────────────────────────────────────────────────────
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
