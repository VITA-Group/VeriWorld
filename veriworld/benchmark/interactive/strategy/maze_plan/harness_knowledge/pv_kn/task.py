"""MazeNavFPS — harness_knowledge / pv_kn ablation.

Pure-vision + agent-authored knowledge. Each round the agent receives
the previous round's mp4 plus a ``# KNOWLEDGE`` document (rewritten
between rounds by an extra summarizer LLM call) and emits a
pathfinding Python snippet that the harness wraps and runs in UE.

Uses :class:`ComputationalEngine` (per-round UE restart) rather than
:class:`InteractiveEngine` — this harness is per-round-computational
in structure even though the underlying task is navigation.

Per-step call budget: **2** VLM calls (policy + summarizer). The
summarizer is skipped on a PASS round since no further knowledge is
needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from veriworld.common import RunLogger, VLMClient
from veriworld.infra.computational.engine import ComputationalEngine

from .._common import (
    TASK_ROOT,
    assemble_scene,
    extract_agent_code,
    extract_thought,
    load_knowledge,
    patch_observe_code,
    update_knowledge,
    verify_waypoints,
)

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
HARNESS_DIR = HERE.parent  # mazenavfps/harness_knowledge/

# Engine dispatcher sentinel — tells ``run_parallel`` to use
# ComputationalEngine (per-round restart) rather than InteractiveEngine,
# overriding the "computational in module path" heuristic since this
# harness lives under ``interactive/navigation/`` path-wise.
ENGINE = "computational"


CONDITION_KEYS = {
    "max_rounds": "Number of submission rounds (after the Round-0 observation). Default 5.",
    "settle_timeout": "Seconds to wait for ball-animation log after a round's code runs. Default 60.",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass
class RoundEntry:
    round: int
    thought: str = ""
    agent_code: str = ""
    verify_result: Optional[str] = None
    verify_log: str = ""
    error: Optional[str] = None
    video: Optional[str] = None


@dataclass
class EpisodeResult:
    model: str
    seed: int
    result: str
    rounds_used: int
    trajectory: List[RoundEntry] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers — ffmpeg + rec_dir (adapted from surface_billiards)
# ---------------------------------------------------------------------------
def _latest_h264(rec_dir: Path, since: float) -> Optional[Path]:
    if not rec_dir.exists():
        return None
    cands = [p for p in rec_dir.glob("*.h264") if p.stat().st_mtime > since]
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def _ffmpeg_to_mp4(h264: Path) -> Path:
    mp4 = h264.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(h264), "-c:v", "copy", str(mp4)],
        capture_output=True, timeout=30,
    )
    return mp4


def _harvest_video(engine: ComputationalEngine, since: float,
                   dest_mp4: Path) -> Optional[Path]:
    """Copy + transcode the latest h264 UE produced after ``since`` into
    ``dest_mp4``. Returns the dest path on success, None on failure.
    Idempotent cleanup of scratch files is done by the caller of the
    next round via mtime filtering."""
    h264 = _latest_h264(engine.rec_dir, since=since)
    if not h264 or not h264.exists():
        return None
    local = dest_mp4.parent / h264.name
    for attempt in range(6):
        try:
            shutil.copy2(h264, local)
            break
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == 5:
                return None
            time.sleep(1.0)
    mp4_tmp = _ffmpeg_to_mp4(local)
    if not mp4_tmp.exists() or mp4_tmp.stat().st_size == 0:
        for p in (local, mp4_tmp, h264):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return None
    shutil.copy2(mp4_tmp, dest_mp4)
    for p in (local, mp4_tmp, h264):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return dest_mp4


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run(
    engine: ComputationalEngine,
    seed: int,
    vlm: VLMClient,
    logger: RunLogger,
    **conditions: Any,
) -> EpisodeResult:
    max_rounds = int(conditions.get("max_rounds", 5))
    settle_timeout = float(conditions.get("settle_timeout", 60.0))

    # Generate maze (shared task-level generator).
    from ...generate_params import generate as generate_maze
    grid_size = int(conditions.get("grid_size", 4))
    params = generate_maze(seed=seed, grid_size=grid_size)

    model_name = vlm.name if hasattr(vlm, "name") else vlm.model
    workspace = logger.model_dir(model_name)
    # Per-worker isolation — params + verify scratch directory.
    (workspace / "params.json").write_text(
        json.dumps(params, indent=2), encoding="utf-8")
    (workspace / "lean_verify").mkdir(parents=True, exist_ok=True)

    logger.snapshot_model(
        task_module=__package__,
        model_name=model_name,
        seed=seed,
        resolved_args={
            "seed": seed, "model": model_name,
            "exe": str(engine.exe),
            **conditions,
        },
    )

    task_md = (HARNESS_DIR / "task.md").read_text(encoding="utf-8")
    api_md = (HARNESS_DIR / "api.md").read_text(encoding="utf-8")
    example_code = (HARNESS_DIR / "example.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    waypoints_path = workspace / "waypoints.json"
    trajectory: List[RoundEntry] = []
    last_video_path: Optional[Path] = None
    last_video_round: Optional[int] = None

    # ── Round 0: bird's-eye observe ────────────────────────────────────
    log.info("Round 0 (observe, bird's-eye)")
    try:
        await engine.next_round(level="/Game/Levels/Axis")
        t_record_start = time.time()
        await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
        await engine.python_exec(patch_observe_code(workspace))
        await asyncio.sleep(6)
        await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
        await asyncio.sleep(3)

        observe_path = workspace / "round_00_observe.mp4"
        got = _harvest_video(engine, t_record_start, observe_path)
        if got:
            last_video_path = got
            last_video_round = 0
    except Exception as e:  # noqa: BLE001
        log.warning("Round 0 observe: %s: %s — continuing without observation video",
                    type(e).__name__, e)

    try:
        await engine.close()
    except Exception as e:  # noqa: BLE001
        log.error("Round 0: engine.close() failed: %s: %s", type(e).__name__, e)
    await asyncio.sleep(5)

    # ── Rounds 1..N ────────────────────────────────────────────────────
    for round_num in range(1, max_rounds + 1):
        log.info("Round %d/%d", round_num, max_rounds)

        knowledge = load_knowledge(workspace)
        scene_params_block = (
            f"## Scene parameters\n"
            f"  grid_rows = {params['grid_rows']}\n"
            f"  grid_cols = {params['grid_cols']}\n"
            f"  start_grid = {params['start_grid']}\n"
            f"  goal_grid = {params['goal_grid']}\n"
            f"  cell_size = {params['cell_size']}\n"
        )
        example_block = (
            f"# Working Example (no-wall assumption — will FAIL; shows API shape)\n"
            f"```python\n{example_code}\n```\n"
        )

        if round_num == 1:
            prompt = (
                f"{task_md}\n\n{api_md}\n\n{scene_params_block}\n"
                f"This is Round {round_num}. The attached video is a bird's-eye "
                f"flyover of the entire maze. Study it and reconstruct the "
                f"**complete** {params['grid_rows']}x{params['grid_cols']} grid "
                f"(grey = wall = 1, white = passage = 0). "
                f"Row 0 at top, col 0 at left.\n\n"
                f"{example_block}\n"
                f"Reply with:\n"
                f"thought: <row-by-row description of the maze>\n\n"
                f"```python\n# reconstruct full grid from video, then BFS\n```"
            )
        else:
            video_note = (
                f"Watch the video. Yellow ball = your previous path. "
                f"Walls that turned **bright red** are cells the ball passed "
                f"through — those were misidentified as passages."
                if last_video_round is not None and last_video_round > 0
                else "(No fresh video from last round — UE failed to record. "
                     "Rely on the KNOWLEDGE document below.)"
            )
            prompt = (
                f"# KNOWLEDGE (read this first!)\n{knowledge or '(none yet)'}\n\n"
                f"{video_note}\n\n"
                f"{api_md}\n\n{scene_params_block}\n"
                f"This is Round {round_num}.\n\n"
                f"{example_block}\n"
                f"Reply with:\n"
                f"thought: <what you see + what knowledge tells you>\n\n"
                f"```python\n# grid with ALL known walls marked, then BFS\n```"
            )

        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            (f"[video: {last_video_path.name}]\n\n" if last_video_path else "")
            + prompt, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        try:
            if last_video_path is not None:
                try:
                    response = await loop.run_in_executor(
                        None, vlm.chat_with_video, prompt, last_video_path,
                    )
                except NotImplementedError:
                    # Non-video-capable transport (e.g. OpenAI-compat
                    # aggregator). Fall back to text-only so the run
                    # still completes — the model is handicapped (no
                    # visual feedback) but we get a comparable
                    # trajectory. Log once per round so the handicap
                    # is visible in the run output.
                    log.warning(
                        "Round %d: %s doesn't support video input — "
                        "falling back to text-only (model sees no video)",
                        round_num, vlm.model,
                    )
                    prompt_with_note = (
                        f"[NOTE: video input is not available for this model; "
                        f"rely on the KNOWLEDGE document and scene parameters "
                        f"below — no visual feedback this round.]\n\n{prompt}"
                    )
                    messages = [{"role": "user", "content": [
                        {"type": "text", "text": prompt_with_note}]}]
                    response = await loop.run_in_executor(None, vlm.chat, messages)
            else:
                messages = [{"role": "user", "content": [
                    {"type": "text", "text": prompt}]}]
                response = await loop.run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            log.error("VLM error round %d: %s", round_num, e)
            trajectory.append(RoundEntry(round=round_num, error=str(e)))
            continue
        log.info("VLM policy %.1fs", time.time() - t0)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(
            response, encoding="utf-8")
        thought = extract_thought(response)
        (workspace / f"round_{round_num:02d}_thought.txt").write_text(
            thought, encoding="utf-8")

        agent_code = extract_agent_code(response)
        if not agent_code:
            trajectory.append(RoundEntry(
                round=round_num, thought=thought,
                error="no ```python code block found in response"))
            continue

        # Syntax-check the agent's snippet before handing it to UE.
        try:
            compile(agent_code, "<agent>", "exec")
        except SyntaxError as e:
            trajectory.append(RoundEntry(
                round=round_num, thought=thought, agent_code=agent_code,
                error=f"SyntaxError: {e}"))
            continue

        full_script = assemble_scene(agent_code, workspace)
        (workspace / f"round_{round_num:02d}_scene.py").write_text(
            full_script, encoding="utf-8")
        (workspace / f"round_{round_num:02d}_code.py").write_text(
            agent_code, encoding="utf-8")

        # Clean any stale log + waypoints from a previous round.
        for p in (log_for_verify, waypoints_path):
            if p.exists():
                p.unlink()

        # ── Per-round UE lifecycle + scene execution ──────────────────
        result: Optional[str] = None
        verify_log_str = ""
        round_error: Optional[str] = None
        new_video_path: Optional[Path] = None
        t_record_start = 0.0
        try:
            await engine.next_round(level="/Game/Levels/Axis")
            t_record_start = time.time()
            await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
            await engine.python_exec(full_script)

            # Poll for result — scene writes "# RESULT: PASS/FAIL" to
            # log_for_verify.txt when the ball reaches the final
            # waypoint (or the 60-s max_duration tick-task timeout hits).
            deadline = time.monotonic() + settle_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if log_for_verify.exists():
                    try:
                        content = log_for_verify.read_text(encoding="utf-8")
                    except Exception:  # noqa: BLE001
                        continue
                    if "# RESULT:" in content:
                        break

            await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
            await asyncio.sleep(3)

            video_path = workspace / f"round_{round_num:02d}_video.mp4"
            got = _harvest_video(engine, t_record_start, video_path)
            if got:
                new_video_path = got
        except Exception as e:  # noqa: BLE001
            round_error = f"UE/WS fault mid-round: {type(e).__name__}: {e}"
            log.warning("Round %d: %s", round_num, round_error)

        try:
            await engine.close()
        except Exception as e:  # noqa: BLE001
            log.error("Round %d: engine.close() failed: %s: %s",
                      round_num, type(e).__name__, e)
        await asyncio.sleep(5)

        # Strict Bresenham wall-crossing verify using the waypoints
        # the scene wrote to disk. This is the source of truth; the
        # in-scene "NAVIGATION_PASS/FAIL" line only measures
        # final-position distance and misses mid-path wall crossings.
        if waypoints_path.exists():
            try:
                wp_data = json.loads(waypoints_path.read_text(encoding="utf-8"))
                waypoints = wp_data.get("waypoints", [])
                result, verify_log_str = verify_waypoints(waypoints, params)
            except Exception as e:  # noqa: BLE001
                verify_log_str = f"verify error: {type(e).__name__}: {e}"
        else:
            verify_log_str = "no waypoints.json — scene did not run to completion"

        (workspace / f"round_{round_num:02d}_verify.txt").write_text(
            verify_log_str, encoding="utf-8")

        entry = RoundEntry(
            round=round_num,
            thought=thought,
            agent_code=agent_code,
            verify_result=result,
            verify_log=verify_log_str,
            error=round_error,
            video=str(new_video_path) if new_video_path else None,
        )
        trajectory.append(entry)

        log.info("Round %d: %s", round_num, round_error or result or "TIMEOUT")

        if new_video_path is not None:
            last_video_path = new_video_path
            last_video_round = round_num

        if result == "PASS":
            break

        # Knowledge summarizer — only on FAIL rounds.
        try:
            t1 = time.time()
            update_knowledge(vlm, workspace,
                             [e.__dict__ for e in trajectory],
                             params["grid_rows"], params["grid_cols"])
            log.info("Round %d: knowledge update %.1fs", round_num, time.time() - t1)
        except Exception as e:  # noqa: BLE001
            log.warning("Round %d: knowledge update failed: %s: %s",
                        round_num, type(e).__name__, e)

    # ── Summary ────────────────────────────────────────────────────────
    final_result = "PASS" if any(e.verify_result == "PASS" for e in trajectory) else "FAIL"
    summary = {
        "task": __package__, "model": model_name, "seed": seed,
        "max_rounds": max_rounds, "result": final_result,
        "rounds_used": len(trajectory),
        "trajectory": [e.__dict__ for e in trajectory],
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("%s: %s in %d rounds", vlm.model, final_result, len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory), trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "CONDITION_KEYS"]
