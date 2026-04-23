"""SurfaceBilliards — putt a ball across a noisy terrain into a target crater.

Ported from ``harness_win_billiards``. The task is per-round computational:

1. **Round 0 (observe)**: engine runs a terrain flyover, records video,
   extracts frames, presents them to the agent.
2. **Rounds 1..N**: the agent submits a complete Python script that
   parameterises the ball's initial velocity (``v_angle``, ``v_speed``)
   and launches it; the engine records the shot, a Slang compute shader
   simulates physics deterministically, and a log file flags
   ``PASS`` / ``FAIL``.
3. UE is **fully restarted between every round** so shader / GPU state
   is clean — this is what :class:`ComputationalEngine` handles.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, List, Optional

from veriworld.common import RunLogger, VLMClient, format_prompt_txt
from veriworld.infra.computational.engine import ComputationalEngine

log = logging.getLogger(__name__)

HERE = Path(__file__).parent


CONDITION_KEYS = {
    "max_rounds": "Number of submission rounds (+ 1 observation round). Default 5.",
    "n_frames": "Frames to extract from each video for the grid. Default 6.",
    "settle_timeout": "Seconds to wait for PASS/FAIL log after submission. Default 60.",
}


def _latest_h264(rec_dir: Path, since: float = 0.0) -> Optional[Path]:
    """Return the most-recently-modified ``.h264`` in ``rec_dir`` whose
    mtime is strictly greater than ``since`` (an epoch timestamp). The
    threshold is essential when multiple UE instances share the same
    ``Saved/Recordings/`` scratch: a worker must ignore files produced
    by peers before its own recording started."""
    if not rec_dir.exists():
        return None
    candidates = [p for p in rec_dir.glob("*.h264") if p.stat().st_mtime > since]
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _collect_recording(
    engine: "ComputationalEngine", since: float,
    dest_mp4: Path, frames_dir: Path, n_frames: int,
    *, log_path: Optional[Path] = None,
    video_start_wallclock: Optional[float] = None,
    extract_frames: bool = True,
) -> List[tuple[Path, str]]:
    """Harvest UE's recording since ``since``, write to ``dest_mp4``,
    extract frames, clean up scratch. Returns ``[(frame_path, label), ...]``.

    If ``log_path`` is given and readable, keyframes are picked at the
    trajectory's *semantic events* (launch / peak / bounces / settle)
    parsed from the log. Otherwise the function falls back to uniform
    sampling across the video duration (used for Round 0 observation
    where no physics log exists yet).

    Pass ``extract_frames=False`` to skip the keyframe step entirely
    (used by the video-only harness path — sends mp4 directly to a
    video-capable model, no frame grid).
    """
    h264_src = _latest_h264(engine.rec_dir, since=since)
    if not h264_src or not h264_src.exists():
        return []
    local_h264 = dest_mp4.parent / h264_src.name

    # Parallel workers share the build's ``Saved/Recordings/`` scratch.
    # Our ``since`` filter already excludes files older than this
    # worker's StartRecording call, but can still resolve to a peer's
    # in-progress h264 if two recordings overlapped in time — at which
    # point Windows returns WinError 32 (file is locked by the other
    # UE). Retry with short backoff; the peer will close the handle
    # within a couple of seconds after its own StopRecording.
    copy_attempts = 6
    for attempt in range(copy_attempts):
        try:
            shutil.copy2(h264_src, local_h264)
            break
        except FileNotFoundError:
            return []
        except PermissionError:
            if attempt == copy_attempts - 1:
                return []
            time.sleep(1.0)
    mp4_tmp = _ffmpeg_to_mp4(local_h264)
    frames: List[tuple[Path, str]] = []
    if mp4_tmp.exists() and mp4_tmp.stat().st_size > 0:
        shutil.copy2(mp4_tmp, dest_mp4)
        if extract_frames:
            timestamps = None
            if log_path is not None and log_path.exists():
                rows = _parse_trajectory_log(log_path)
                if rows:
                    timestamps = _pick_key_timestamps(
                        rows, n_frames,
                        video_start_wallclock=video_start_wallclock,
                    )
            frames = _extract_frames(dest_mp4, n_frames, frames_dir,
                                     timestamps=timestamps)
    for p in (local_h264, mp4_tmp, h264_src):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return frames


def _ffmpeg_to_mp4(h264: Path) -> Path:
    mp4 = h264.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(h264), "-c:v", "copy", str(mp4)],
        capture_output=True, timeout=30,
    )
    return mp4


def _parse_trajectory_log(log_path: Path) -> List[dict]:
    """Read a log_for_verify.txt CSV and return a list of dict rows.

    Supported schemas (old → new):
    - 7 cols: ``frame,elapsed,bx,by,bz,dist,status``  (legacy)
    - 8 cols: ``frame,elapsed,wallclock,bx,by,bz,dist,status``  (current)

    ``wallclock`` is ``time.time()`` at the moment of the log write;
    it lets the harness convert physics-log times to video-playback
    times when NVENC drops frames under GPU load (tick-elapsed and
    video-elapsed diverge by seconds, log-wallclock stays ground truth).
    Rows without a wallclock field fall back to ``None`` — downstream
    keyframe picking then uses the tick elapsed time as a rough proxy.
    """
    rows: List[dict] = []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        parts = line.strip().split(",")
        if parts[0] == "frame" or parts[0].startswith("#"):
            continue
        try:
            if len(parts) == 8:
                rows.append({
                    "frame": int(parts[0]),
                    "t": float(parts[1]),
                    "wallclock": float(parts[2]),
                    "x": float(parts[3]),
                    "y": float(parts[4]),
                    "z": float(parts[5]),
                    "dist": float(parts[6]),
                    "status": parts[7].strip(),
                })
            elif len(parts) == 7:
                rows.append({
                    "frame": int(parts[0]),
                    "t": float(parts[1]),
                    "wallclock": None,
                    "x": float(parts[2]),
                    "y": float(parts[3]),
                    "z": float(parts[4]),
                    "dist": float(parts[5]),
                    "status": parts[6].strip(),
                })
        except ValueError:
            continue
    return rows


_LANDING_OFFSETS: List[tuple[str, float]] = [
    ("pre_3",   -0.6),
    ("pre_2",   -0.4),
    ("pre_1",   -0.2),
    ("landing",  0.0),
    ("post_1",  +0.3),
    ("post_2",  +0.6),
]


def _row_video_t(row: dict, video_start_wallclock: Optional[float]) -> float:
    """Convert a log row to video-playback time. Prefers wallclock
    (accurate when NVENC drops frames), falls back to tick elapsed."""
    wc = row.get("wallclock")
    if wc is not None and video_start_wallclock is not None:
        return wc - video_start_wallclock
    return row["t"]


def _landing_row(rows: List[dict]) -> Optional[dict]:
    """The last ``flying`` row — that's the moment just before the ball
    came to rest. Settle detection fires ~0.5 s later (after 30 motion-
    less frames) so the ``settled`` entry is not the true landing."""
    for row in reversed(rows):
        if row.get("status") == "flying":
            return row
    return rows[-1] if rows else None


def _pick_key_timestamps(
    rows: List[dict], n_desired: int,
    *, video_start_wallclock: Optional[float] = None,
) -> List[tuple[str, float]]:
    """Return ``[(label, video_t), ...]`` anchored to the landing moment.

    Six tiles: three pre-landing (0.6/0.4/0.2 s before), landing, two
    post-landing (0.3/0.6 s after). Each ``video_t`` is in the video's
    playback-time frame of reference so ffmpeg extraction lands on the
    right frame even when NVENC dropped frames during recording (log's
    tick-elapsed would otherwise run ahead of video-elapsed).

    Falls back to tick elapsed when ``video_start_wallclock`` is None
    or the log pre-dates the wallclock column.
    """
    if not rows:
        return []

    landing_row = _landing_row(rows)
    if landing_row is None:
        return []

    landing_video_t = _row_video_t(landing_row, video_start_wallclock)
    earliest_video_t = _row_video_t(rows[0], video_start_wallclock)

    picked: List[tuple[str, float]] = []
    for label, offset in _LANDING_OFFSETS:
        t = max(earliest_video_t, landing_video_t + offset)
        picked.append((label, t))

    # Dedupe nearly-identical picks (< 50 ms apart).
    deduped: List[tuple[str, float]] = []
    for label, t in picked:
        if deduped and abs(deduped[-1][1] - t) < 0.05:
            continue
        deduped.append((label, t))

    deduped.sort(key=lambda e: e[1])
    return deduped[:n_desired]


def _extract_frames(
    video: Path, n: int, out_dir: Path,
    *, timestamps: Optional[List[tuple[str, float]]] = None,
) -> List[tuple[Path, str]]:
    """Extract frames and return ``[(path, label), ...]``. If
    ``timestamps`` is supplied the frames are taken at those exact times
    with human-readable labels encoded into the filename; otherwise
    uniform sampling across the video duration (labels ``t00``..``tNN``).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    if timestamps:
        plan = list(timestamps)
    else:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(r.stdout.strip())
        except Exception:  # noqa: BLE001
            duration = 10.0
        plan = [(f"t{i:02d}", duration * (i + 0.5) / n) for i in range(n)]

    frames: List[tuple[Path, str]] = []
    for idx, (label, t) in enumerate(plan):
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in label)
        p = out_dir / f"frame_{idx:02d}_{safe}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(p)],
            capture_output=True, timeout=15,
        )
        if p.exists():
            frames.append((p, label))
    return frames


def _make_frame_grid(frame_entries: List[tuple[Path, str]]) -> Optional[str]:
    """Return a data-URL PNG with each tile labelled by its event name."""
    from PIL import Image, ImageDraw  # local import

    if not frame_entries:
        return None
    imgs = [Image.open(p).resize((480, 270), Image.LANCZOS)
            for (p, _) in frame_entries]
    labels = [lbl for (_, lbl) in frame_entries]
    cols = 2
    rows = (len(imgs) + cols - 1) // cols
    w, h = imgs[0].size
    pad = 20
    grid = Image.new("RGB", (cols * w, rows * (h + pad)), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    for idx, (img, label) in enumerate(zip(imgs, labels)):
        r, c = divmod(idx, cols)
        x = c * w
        y = r * (h + pad)
        draw.text((x + 5, y + 2), label, fill=(0, 0, 0))
        grid.paste(img, (x, y + pad))
    buf = BytesIO()
    grid.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


_LEGACY_BASE = (
    'BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/'
    'unreal_projects_lean/lean/unit_tests/17_surface_billiards_hard"'
)


def _patch_base_path(code: str, workspace: Path) -> str:
    """Rewrite the legacy BASE placeholder to a **per-worker** workspace
    so parallel batches don't race on shared params/log files."""
    return code.replace(_LEGACY_BASE, f'BASE = r"{workspace}"')


_SHOT_PARAM_RE = re.compile(r"^\s*(v_angle|v_speed)\s*:\s*([-+\d.eE]+)", re.MULTILINE)
_KNOWLEDGE_RE = re.compile(
    r"^\s*knowledge\s*:\s*(.*?)(?=^\s*(?:v_angle|v_speed|observation)\s*:|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _extract_knowledge(response: str) -> Optional[str]:
    """Pull the free-form ``knowledge:`` block from a VLM response so it
    can be archived in ``knowledge.md``. Returns ``None`` if absent."""
    m = _KNOWLEDGE_RE.search(response)
    if not m:
        return None
    body = m.group(1).strip()
    return body or None


def _parse_shot_params(response: str) -> tuple[Optional[float], Optional[float]]:
    """Extract the ``v_angle`` and ``v_speed`` scalars from a VLM
    response. Expects line-based format like::

        v_angle: 0.87
        v_speed: 240.0

    Returns ``(None, None)`` if neither is found."""
    angle: Optional[float] = None
    speed: Optional[float] = None
    for m in _SHOT_PARAM_RE.finditer(response):
        key, raw = m.group(1), m.group(2)
        try:
            val = float(raw)
        except ValueError:
            continue
        if key == "v_angle":
            angle = val
        else:
            speed = val
    return angle, speed


def _seed_workspace(workspace: Path, params: dict) -> None:
    """Populate a workspace with everything the agent's submitted script
    expects under ``BASE/``: its own ``params.json`` and a private copy
    of the ground-truth shader (so concurrent workers don't collide on
    ``lean_verify/log_for_verify.txt``)."""
    (workspace / "params.json").write_text(
        json.dumps(params, indent=2), encoding="utf-8")
    verify_dir = workspace / "lean_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    shader_src = HERE / "lean_verify" / "bouncy_ball.slang"
    shutil.copy2(shader_src, verify_dir / "bouncy_ball.slang")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class RoundEntry:
    round: int
    v_angle: Optional[float] = None
    v_speed: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class EpisodeResult:
    model: str
    seed: int
    result: str
    rounds_used: int
    trajectory: List[RoundEntry] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


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
    n_frames = int(conditions.get("n_frames", 6))
    settle_timeout = float(conditions.get("settle_timeout", 60.0))

    # Generate params (terrain + target)
    from .generate_params import generate as generate_params
    params = generate_params(seed)

    model_name = vlm.name if hasattr(vlm, "name") else vlm.model
    workspace = logger.model_dir(model_name)
    # Per-worker isolation — params + private shader copy + private verify
    # log path, so parallel batches don't race on HERE/lean_verify files.
    _seed_workspace(workspace, params)

    # Per-model reproducibility snapshot: config.json + reproduce.bat/sh.
    # Include the build path so the reproducer is runnable as-is (every
    # per-task __main__ requires --exe).
    logger.snapshot_model(
        task_module=__package__,
        model_name=model_name,
        seed=seed,
        resolved_args={
            "seed": seed,
            "model": model_name,
            "exe": str(engine.exe),
            **conditions,
        },
    )

    task_md = (HERE / "task.md").read_text(encoding="utf-8")
    shot_template = (HERE / "setup_shot.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    trajectory: List[RoundEntry] = []
    # Most recently harvested mp4 — sent to the VLM as video-only
    # feedback. Crashed rounds do NOT advance it, so the prompt always
    # references a video that actually exists on disk.
    last_video_path: Optional[Path] = None
    last_video_round: Optional[int] = None
    # Accumulated agent reasoning — each round's ``knowledge:`` block
    # is captured here and flushed to ``knowledge.md`` at run end.
    knowledge_blocks: List[tuple[int, str]] = []

    # ── Round 0: observe ────────────────────────────────────────────────
    # Same crash-tolerance contract as Round N: any UE / WS / file-copy
    # fault is logged and we proceed with whatever video we managed to
    # collect (possibly nothing). Round 1's prompt handles a missing
    # observation (``last_video_path`` stays None — text-only call).
    log.info("Round 0 (observe)")
    t_record_start = 0.0
    try:
        await engine.next_round(level="/Game/Levels/Axis")
        t_record_start = time.time()
        await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
        setup_code = _patch_base_path(
            (HERE / "setup_observe.py").read_text(encoding="utf-8"), workspace)
        await engine.python_exec(setup_code)
        await asyncio.sleep(8)
        await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
        await asyncio.sleep(3)

        observe_path = workspace / "round_00_observe.mp4"
        _collect_recording(
            engine, t_record_start, observe_path,
            workspace / "round_00_frames", n_frames,
            extract_frames=False,
        )
        if observe_path.exists() and observe_path.stat().st_size > 0:
            last_video_path = observe_path
            last_video_round = 0
    except Exception as e:  # noqa: BLE001
        log.warning("Round 0 observe: %s: %s — continuing without observation video",
                    type(e).__name__, e)

    try:
        await engine.close()
    except Exception as e:  # noqa: BLE001
        log.error("Round 0: engine.close() failed: %s: %s — "
                  "leftover UE may contaminate rec_dir scratch on Round 1",
                  type(e).__name__, e)
    await asyncio.sleep(5)

    # ── Rounds 1..N ─────────────────────────────────────────────────────
    for round_num in range(1, max_rounds + 1):
        log.info("Round %d/%d", round_num, max_rounds)

        # Build prompt — parameter-only format (no code snippets).
        # Video is the sole visual feedback; ``last_video_round`` is the
        # source of truth for what the agent is actually looking at (may
        # lag ``round_num - 1`` if the immediately-preceding round
        # crashed without producing video).
        if last_video_round is None:
            video_label = "no observation video available (UE failed during observation flyover)"
        elif last_video_round == 0:
            video_label = "Round 0 observation flyover (no shot yet — terrain only)"
        else:
            video_label = f"Round {last_video_round}"

        if round_num == 1:
            prompt = (
                f"{task_md}\n\n"
                f"This is Round {round_num}. The attached video is the "
                f"**{video_label}**. Use it to read the terrain and the A→B "
                f"geometry, then choose your first shot. Remember the response "
                f"format (observation / knowledge / v_angle / v_speed)."
            )
        else:
            measured = [t for t in trajectory
                        if not (t.result is None and t.error is not None)]
            crashed = [t.round for t in trajectory
                       if t.result is None and t.error is not None]
            lines = ["| Round | v_angle (rad) | v_speed (cm/s) | result |",
                     "|-------|---------------|----------------|--------|"]
            for t in measured:
                ang = f"{t.v_angle:.3f}" if isinstance(t.v_angle, (int, float)) else "?"
                spd = f"{t.v_speed:.1f}" if isinstance(t.v_speed, (int, float)) else "?"
                res = t.result or "?"
                lines.append(f"| R{t.round} | {ang} | {spd} | {res} |")
            shot_table = "\n".join(lines)
            if crashed:
                crashed_str = ", ".join(f"R{r}" for r in crashed)
                shot_table += (
                    f"\n\n*Note: round(s) {crashed_str} crashed mid-execution "
                    f"and produced no measurement — not shown above. Treat their "
                    f"submitted angle/speed as having no information.*"
                )
            prompt = (
                f"{task_md}\n\n"
                f"# Shot history\n{shot_table}\n\n"
                f"This is Round {round_num}. The attached video is the "
                f"**{video_label}**. Update your ``knowledge`` from what you see, "
                f"then choose the next shot."
            )

        # Archive the prompt verbatim. With video-only feedback the
        # message format is simpler than the OpenAI image_url form, so we
        # write the text alongside a pointer to the video file.
        prompt_archive = prompt
        if last_video_path is not None:
            prompt_archive = f"[video: {last_video_path.name}]\n\n{prompt}"
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            prompt_archive, encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        try:
            if last_video_path is not None:
                response = await loop.run_in_executor(
                    None, vlm.chat_with_video, prompt, last_video_path,
                )
            else:
                # No video available — fall back to text-only call.
                messages = [{"role": "user", "content": [
                    {"type": "text", "text": prompt}]}]
                response = await loop.run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            log.error("VLM error round %d: %s", round_num, e)
            trajectory.append(RoundEntry(round=round_num, error=str(e)))
            continue
        log.info("VLM %.1fs", time.time() - t0)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(response, encoding="utf-8")

        k_block = _extract_knowledge(response)
        if k_block:
            knowledge_blocks.append((round_num, k_block))

        v_angle, v_speed = _parse_shot_params(response)
        if v_angle is None or v_speed is None:
            trajectory.append(RoundEntry(
                round=round_num, v_angle=v_angle, v_speed=v_speed,
                error=f"could not parse v_angle/v_speed from response "
                      f"(got angle={v_angle}, speed={v_speed})"))
            continue

        # Substitute placeholders; save the final executable for audit.
        code_text = _patch_base_path(
            shot_template
            .replace("{{V_ANGLE}}", f"{v_angle}")
            .replace("{{V_SPEED}}", f"{v_speed}"),
            workspace,
        )
        (workspace / f"round_{round_num:02d}_code.py").write_text(
            code_text, encoding="utf-8")

        if log_for_verify.exists():
            log_for_verify.unlink()

        # ── Per-round UE lifecycle + physics simulation ─────────────────
        # If UE crashes anywhere in here (mid-settle, mid-StopRecording,
        # mid-video-harvest), we record the round as an error with
        # whatever partial data survived, force-close the engine, and
        # let the loop proceed — next round's next_round() relaunches a
        # fresh UE so the worker isn't burned for the whole episode.
        result: Optional[str] = None
        new_video_path: Optional[Path] = None
        round_error: Optional[str] = None
        round_log: Optional[Path] = None
        t_record_start = 0.0
        try:
            await engine.next_round(level="/Game/Levels/Axis")
            t_record_start = time.time()
            await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
            await engine.python_exec(code_text)

            # Poll for PASS/FAIL
            deadline = time.monotonic() + settle_timeout
            while time.monotonic() < deadline:
                await asyncio.sleep(1)
                if log_for_verify.exists():
                    try:
                        content = log_for_verify.read_text(encoding="utf-8")
                    except Exception:  # noqa: BLE001
                        continue
                    if "# RESULT: PASS" in content:
                        result = "PASS"
                        break
                    if "# RESULT: FAIL" in content:
                        result = "FAIL"
                        break

            await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
            await asyncio.sleep(3)

            # Archive per-round log before scratch is overwritten.
            if log_for_verify.exists():
                round_log = workspace / f"round_{round_num:02d}_log.txt"
                shutil.copy2(log_for_verify, round_log)

            video_path = workspace / f"round_{round_num:02d}_video.mp4"
            _collect_recording(
                engine, t_record_start, video_path,
                workspace / f"round_{round_num:02d}_frames", n_frames,
                log_path=round_log,
                video_start_wallclock=t_record_start,
                extract_frames=False,
            )
            if video_path.exists() and video_path.stat().st_size > 0:
                new_video_path = video_path
        except Exception as e:  # noqa: BLE001
            round_error = f"UE/WS fault mid-round: {type(e).__name__}: {e}"
            log.warning("Round %d: %s — continuing to next round", round_num, round_error)
            # Salvage: partial log, if the tick task wrote anything before UE died.
            if log_for_verify.exists():
                round_log = workspace / f"round_{round_num:02d}_log.txt"
                try:
                    shutil.copy2(log_for_verify, round_log)
                except OSError:
                    round_log = None

        # Always tear down this round's UE — next round will relaunch.
        # Do NOT swallow exceptions: ``engine.close()`` is what guarantees
        # the previous UE is fully dead before the next ``StartRecording``,
        # so a silent failure here is exactly the case where leftover
        # h264 from this round could survive into the next round's
        # mtime filter and contaminate the agent's frames.
        try:
            await engine.close()
        except Exception as e:  # noqa: BLE001
            log.error("Round %d: engine.close() failed: %s: %s — "
                      "leftover UE may contaminate rec_dir scratch on next round",
                      round_num, type(e).__name__, e)
        await asyncio.sleep(5)

        # Only advance last_video_path on a successful harvest; a crashed
        # round leaves the agent looking at the last good video, and
        # ``last_video_round`` records *which* round it's from so the
        # prompt can label it honestly.
        if new_video_path is not None:
            last_video_path = new_video_path
            last_video_round = round_num

        trajectory.append(RoundEntry(
            round=round_num, v_angle=v_angle, v_speed=v_speed,
            result=result, error=round_error,
        ))
        log.info("Round %d: %s (angle=%s speed=%s)",
                 round_num,
                 (round_error or result or "TIMEOUT"),
                 v_angle, v_speed)

        if result == "PASS":
            break

    # Summary
    final_result = "PASS" if any(t.result == "PASS" for t in trajectory) else "FAIL"
    summary = {
        "task": __package__,
        "model": model_name, "seed": seed, "max_rounds": max_rounds,
        "result": final_result, "rounds_used": len(trajectory),
        "trajectory": [t.__dict__ for t in trajectory],
    }
    (workspace / "episode_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )

    # knowledge.md — agent's accumulated reasoning across rounds.
    k_lines: List[str] = [
        f"# Accumulated knowledge — {model_name} on seed {seed}",
        "",
        f"Final result: **{final_result}** in {len(trajectory)} round(s).",
        "",
    ]
    for round_num, body in knowledge_blocks:
        k_lines.append(f"## Round {round_num}")
        k_lines.append("")
        k_lines.append(body)
        k_lines.append("")
    (workspace / "knowledge.md").write_text("\n".join(k_lines), encoding="utf-8")
    log.info("%s: %s in %d rounds", vlm.model, final_result, len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory), trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "CONDITION_KEYS"]
