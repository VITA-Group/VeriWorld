"""DropToTarget — pure-visual ablation.

Per-round computational task. The agent watches a video of the previous
round (rendered into a frame grid), then writes a **complete Python
script** that deforms a GPUClothActor surface so a ball rolls off and
lands inside a red target circle on the ground.

Pure-visual means: the agent receives *no* numerical target coordinates
in the prompt. Direction and distance must be estimated from the frames.
``generate_params`` still writes ``params.json`` alongside the task — but
only so the agent's own submitted script can load scene metadata
(``surface_z``, ``target`` for the spawn marker, etc.). The prompt never
reveals ``target`` to the VLM.

Ablation boundary (see benchmark README): a condition that swaps text
targets for pure video is an *action-space / knowledge-org* change, so
this lives in its own ``visual/`` folder rather than being a CLI flag on
a sibling task.
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
TASK_DIR = HERE.parent  # the drop_to_target/ root where task.md / api.md live


CONDITION_KEYS = {
    "max_rounds": "Number of submission rounds (+ 1 observation round). Default 5.",
    "n_frames": "Frames to extract from each video for the grid. Default 6.",
    "settle_timeout": "Seconds to wait for PASS/FAIL log after submission. Default 45.",
    "observe_seconds": "How long round 0 records the flat surface + target. Default 5.",
}


# ---------------------------------------------------------------------------
# Recording + frame-grid helpers (mirrors surface_billiards)
# ---------------------------------------------------------------------------
def _latest_h264(rec_dir: Path, since: float = 0.0) -> Optional[Path]:
    """Most-recently-modified ``.h264`` with mtime strictly greater
    than ``since``. The threshold is essential for parallel workers
    sharing the same build-side ``Saved/Recordings/`` scratch."""
    if not rec_dir.exists():
        return None
    candidates = [p for p in rec_dir.glob("*.h264") if p.stat().st_mtime > since]
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _ffmpeg_to_mp4(h264: Path) -> Path:
    mp4 = h264.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(h264), "-c:v", "copy", str(mp4)],
        capture_output=True, timeout=30,
    )
    return mp4


def _collect_recording(
    engine: "ComputationalEngine", since: float,
    dest_mp4: Path, frames_dir: Path, n_frames: int,
) -> List[Path]:
    """Harvest UE's recording since ``since``, land it at ``dest_mp4``,
    extract frames, clean up scratch. Copies the ``.h264`` into the
    per-worker workspace before ffmpeg so parallel workers don't race
    on the shared build-side scratch dir."""
    h264_src = _latest_h264(engine.rec_dir, since=since)
    if not h264_src or not h264_src.exists():
        return []
    local_h264 = dest_mp4.parent / h264_src.name
    try:
        shutil.copy2(h264_src, local_h264)
    except FileNotFoundError:
        return []
    mp4_tmp = _ffmpeg_to_mp4(local_h264)
    frames: List[Path] = []
    if mp4_tmp.exists() and mp4_tmp.stat().st_size > 0:
        shutil.copy2(mp4_tmp, dest_mp4)
        frames = _extract_frames(dest_mp4, n_frames, frames_dir)
    for p in (local_h264, mp4_tmp, h264_src):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return frames


def _extract_frames(video: Path, n: int, out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        duration = 5.0

    frames: List[Path] = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        p = out_dir / f"frame_{i:02d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(p)],
            capture_output=True, timeout=15,
        )
        if p.exists():
            frames.append(p)
    return frames


def _make_frame_grid(frame_paths: List[Path]) -> Optional[str]:
    """Return a data-URL PNG with frames labelled by t=X%."""
    from PIL import Image, ImageDraw  # local import

    if not frame_paths:
        return None
    imgs = [Image.open(p).resize((480, 270), Image.LANCZOS) for p in frame_paths]
    cols = 2
    rows = (len(imgs) + cols - 1) // cols
    w, h = imgs[0].size
    pad = 20
    grid = Image.new("RGB", (cols * w, rows * (h + pad)), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    for idx, img in enumerate(imgs):
        r, c = divmod(idx, cols)
        x = c * w
        y = r * (h + pad)
        t_pct = idx / max(1, len(imgs) - 1) * 100
        draw.text((x + 5, y + 2), f"t={t_pct:.0f}%", fill=(0, 0, 0))
        grid.paste(img, (x, y + pad))
    buf = BytesIO()
    grid.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Code patching — redirect the private-repo BASE path to a **per-worker**
# workspace so parallel orchestrator batches (N models × M seeds) don't
# race on the shared params.json / log_for_verify.txt files. The agent's
# submitted script then reads params and writes its verify log inside its
# own workspace directory.
# ---------------------------------------------------------------------------
_LEGACY_BASE = (
    'BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/'
    'unreal_projects_lean/lean/unit_tests/12c_drop_to_target"'
)


def _patch_base_path(code: str, workspace: Path) -> str:
    return code.replace(_LEGACY_BASE, f'BASE = r"{workspace}"')


def _seed_workspace(workspace: Path, params: dict) -> None:
    """Populate a workspace with everything the agent's submitted script
    expects to find under ``BASE/``: its own ``params.json`` and a
    private copy of the shader so multiple workers don't collide on
    ``lean_verify/log_for_verify.txt``."""
    (workspace / "params.json").write_text(
        json.dumps(params, indent=2), encoding="utf-8")
    verify_dir = workspace / "lean_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    shader_src = TASK_DIR / "lean_verify" / "slide_ball.slang"
    shutil.copy2(shader_src, verify_dir / "slide_ball.slang")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class RoundEntry:
    round: int
    result: Optional[str] = None          # "PASS" | "FAIL" | None (timeout)
    error: Optional[str] = None
    thought: Optional[str] = None
    code_chars: Optional[int] = None


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
    settle_timeout = float(conditions.get("settle_timeout", 45.0))
    observe_seconds = float(conditions.get("observe_seconds", 5.0))

    from ..generate_params import generate as generate_params
    params = generate_params(seed)

    model_name = vlm.name if hasattr(vlm, "name") else vlm.model
    workspace = logger.model_dir(model_name)
    # Per-worker isolation — params + private shader copy + private verify
    # log path, so parallel batches don't race on TASK_DIR files.
    _seed_workspace(workspace, params)

    # Per-model reproducibility snapshot: config.json + reproduce.bat/sh.
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

    task_md_raw = (TASK_DIR / "task.md").read_text(encoding="utf-8")
    task_md = task_md_raw.replace("{surface_z}", str(params["surface_z"]))
    api_md = (TASK_DIR / "api.md").read_text(encoding="utf-8")
    example_code = (TASK_DIR / "example.py").read_text(encoding="utf-8")

    log_for_verify = workspace / "lean_verify" / "log_for_verify.txt"
    trajectory: List[RoundEntry] = []
    last_frame_grid: Optional[str] = None

    level = "/Game/Levels/Axis"

    # ── Round 0: observe ────────────────────────────────────────────────
    log.info("Round 0 (observe): flat surface + ball + target, %.1fs recording", observe_seconds)
    await engine.next_round(level=level)
    t_record_start = time.time()
    await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
    setup_code = _patch_base_path(
        (TASK_DIR / "setup_observe.py").read_text(encoding="utf-8"), workspace)
    await engine.python_exec(setup_code)
    await asyncio.sleep(observe_seconds)
    await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
    await asyncio.sleep(3)

    observe_path = workspace / "round_00_observe.mp4"
    frames = _collect_recording(
        engine, t_record_start, observe_path, workspace / "round_00_frames", n_frames,
    )
    last_frame_grid = _make_frame_grid(frames)
    if last_frame_grid:
        (workspace / "round_00_frames.png").write_bytes(
            base64.b64decode(last_frame_grid.split(",", 1)[1])
        )
    await engine.close()
    await asyncio.sleep(5)

    # ── Rounds 1..N ─────────────────────────────────────────────────────
    for round_num in range(1, max_rounds + 1):
        log.info("Round %d/%d", round_num, max_rounds)

        example_section = (
            f"\n# Working Example (wavy surface — different shape, same API)\n"
            f"```python\n{example_code}\n```\n\n"
        )
        if round_num == 1:
            prompt = (
                f"{task_md}\n\n"
                f"This is Round {round_num}. Watch the frames to see where the "
                f"red target circle is relative to the ball on the green surface. "
                f"You do NOT have the target coordinates — estimate direction and "
                f"distance from what you see.\n\n"
                f"# API Reference\n\n{api_md}\n\n"
                f"{example_section}"
                "IMPORTANT: Output a COMPLETE Python script (the whole template, "
                "with your own surface-shape math replacing the YOUR SURFACE SHAPE "
                "HERE section).\n\n"
                "Reply with:\nthought: <your approach>\n```python\n<complete script>\n```"
            )
        else:
            history_lines = ["| Round | Result | Thought |",
                             "|-------|--------|---------|"]
            for t in trajectory:
                r = t.result or t.error or "timeout"
                th = (t.thought or "").replace("\n", " ").strip()[:80]
                history_lines.append(f"| R{t.round} | {r} | {th} |")
            history = "\n".join(history_lines)
            prompt = (
                f"# History so far\n{history}\n\n"
                f"Watch these frames from Round {round_num - 1}. The ball should "
                f"land inside the red target circle on the ground. Where did it "
                f"actually land relative to the target? Adjust your surface "
                f"deformation.\n\n"
                f"This is Round {round_num}.\n\n"
                f"# API Reference\n\n{api_md}\n\n"
                f"{example_section}"
                "IMPORTANT: Output a COMPLETE Python script.\n\n"
                "Reply with:\nthought: <analysis>\n```python\n<complete script>\n```"
            )

        parts: list = []
        if last_frame_grid:
            label = "observation (Round 0)" if round_num == 1 else f"Round {round_num - 1}"
            parts.append({"type": "text", "text": f"**Frames from {label}:**"})
            parts.append({"type": "image_url", "image_url": {"url": last_frame_grid}})
        parts.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": parts}]

        prev_frames_file = f"round_{(round_num - 1):02d}_frames.png"
        (workspace / f"round_{round_num:02d}_prompt.txt").write_text(
            format_prompt_txt(messages, [prev_frames_file] if last_frame_grid else []),
            encoding="utf-8")

        loop = asyncio.get_event_loop()
        t0 = time.time()
        try:
            response = await loop.run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            log.error("VLM error round %d: %s", round_num, e)
            trajectory.append(RoundEntry(round=round_num, error=str(e)))
            continue
        log.info("VLM %.1fs", time.time() - t0)

        (workspace / f"round_{round_num:02d}_response.txt").write_text(response, encoding="utf-8")

        thought = response.split("```")[0].strip()[:400]
        (workspace / f"round_{round_num:02d}_thought.txt").write_text(thought, encoding="utf-8")

        code_match = re.search(r"```python\s*\n(.*?)```", response, re.DOTALL)
        if not code_match:
            trajectory.append(RoundEntry(round=round_num, thought=thought,
                                         error="no python code block"))
            continue

        code_text = _patch_base_path(code_match.group(1).strip(), workspace)
        code_path = workspace / f"round_{round_num:02d}_code.py"
        code_path.write_text(code_text, encoding="utf-8")

        try:
            compile(code_text, str(code_path), "exec")
        except SyntaxError as e:
            trajectory.append(RoundEntry(round=round_num, thought=thought,
                                         code_chars=len(code_text),
                                         error=f"SyntaxError: {e}"))
            continue

        if log_for_verify.exists():
            log_for_verify.unlink()

        # Launch fresh UE + submit agent code
        await engine.next_round(level=level)
        t_record_start = time.time()
        await engine.python_exec("import unreal_runtime as ur; ur.StartRecording(codec='h264')")
        await engine.python_exec(code_text)

        # Poll for PASS/FAIL (the in-scene tick writes `# RESULT: X` on settle
        # and `LANDED_PASS/FAIL` on first ground touch; settle is the
        # authoritative signal).
        result: Optional[str] = None
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
                # Fallback: first-touch tag if the ball doesn't settle in time.
                if result is None:
                    if "LANDED_PASS" in content:
                        result = "PASS"
                    elif "LANDED_FAIL" in content:
                        result = "FAIL"

        await engine.python_exec("import unreal_runtime as ur; ur.StopRecording()")
        await asyncio.sleep(3)

        video_path = workspace / f"round_{round_num:02d}_video.mp4"
        frames = _collect_recording(
            engine, t_record_start, video_path,
            workspace / f"round_{round_num:02d}_frames", n_frames,
        )
        last_frame_grid = _make_frame_grid(frames)
        if last_frame_grid:
            (workspace / f"round_{round_num:02d}_frames.png").write_bytes(
                base64.b64decode(last_frame_grid.split(",", 1)[1])
            )

        if log_for_verify.exists():
            shutil.copy2(log_for_verify, workspace / f"round_{round_num:02d}_log.txt")

        await engine.close()
        await asyncio.sleep(5)

        trajectory.append(RoundEntry(round=round_num, thought=thought,
                                     code_chars=len(code_text), result=result))
        log.info("Round %d: %s", round_num, result or "TIMEOUT")

        if result == "PASS":
            break

    # Summary
    final_result = "PASS" if any(t.result == "PASS" for t in trajectory) else "FAIL"
    summary = {
        "task": "drop_to_target.visual",
        "model": vlm.model, "seed": seed, "max_rounds": max_rounds,
        "result": final_result, "rounds_used": len(trajectory),
        "params": params,
        "trajectory": [t.__dict__ for t in trajectory],
    }
    (workspace / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("%s: %s in %d rounds", vlm.model, final_result, len(trajectory))

    return EpisodeResult(
        model=vlm.model, seed=seed, result=final_result,
        rounds_used=len(trajectory), trajectory=trajectory,
        artifacts={"workspace": str(workspace)},
    )


__all__ = ["run", "EpisodeResult", "RoundEntry", "CONDITION_KEYS"]
