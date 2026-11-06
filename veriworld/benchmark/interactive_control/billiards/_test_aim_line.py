"""Visual smoke test for the aim-line preview capability.

Spawns the standard billiards scene, then for each test angle:
  1. Spawns a thin red aim-line beam from the cue ball at that angle.
  2. Snaps a top-down screenshot.
  3. Clears the aim line.

Outputs land in ``./_aim_line_test/<TIMESTAMP>/aim_NNN_deg.png`` so you
can flip through them and verify visually that:
  - the line emanates FROM the cue ball center,
  - the angle convention matches shots (0 = +X = toward rack),
  - the line is visible from the camera (not too thin, not occluded).

Usage:
    "...PackagedOutput_dev/Windows/demo1.exe" demo1 \
        -AudioMixer -WebSocketPort=9003 -windowed \
        -ResX=640 -ResY=480 -ForceRes -nosplash

    python -m veriworld.benchmark.interactive_control.billiards._test_aim_line \
        --exe "C:/.../PackagedOutput_dev/Windows/demo1.exe" \
        --port 9003
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List

from veriworld.infra.interactive.engine import InteractiveEngine

from ._shared import (
    assemble_aim_line,
    assemble_clear_aim_line,
    assemble_setup,
)
from .harness_image._common import harvest_screenshot, screenshot_dir_from_exe
from .generate_params import generate as generate_params

log = logging.getLogger("aim_line_test")


# Angles to render — covers cardinals + a few intermediates so we can
# confirm the rotation direction matches the shot convention
# (0=+X toward rack, +90=+Y screen-right, -90=-Y screen-left).
TEST_ANGLES_DEG: List[float] = [0, 30, 45, 90, 135, 180, -45, -90, -135]


async def _amain(exe: Path, port: int, seed: int, difficulty: str,
                 length_cm: float, out_root: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # MUST be absolute: SCENE_SETUP_CODE runs inside UE whose working
    # directory is the build install dir, not this script's cwd. A
    # relative BASE makes UE silently fail to read params.json.
    workspace = (out_root / ts).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # Generate scene params and write to workspace so SCENE_SETUP_CODE
    # (which loads params.json from BASE) can find them.
    params = generate_params(seed=seed, difficulty=difficulty)
    (workspace / "params.json").write_text(
        json.dumps(params, indent=2), encoding="utf-8")
    log.info("workspace: %s", workspace)

    engine = await InteractiveEngine.attach(f"ws://127.0.0.1:{port}")
    shot_dir = screenshot_dir_from_exe(exe)

    try:
        try:
            await engine.switch_level("/Game/Levels/Untitled")
        except Exception as e:  # noqa: BLE001
            log.warning("level switch failed: %s", e)
        await asyncio.sleep(2)

        log.info("setting up scene (difficulty=%s, seed=%d)", difficulty, seed)
        await engine.python_exec(assemble_setup(workspace))
        await asyncio.sleep(2)

        # Reference screenshot with no aim line for comparison.
        ref_png = workspace / "00_no_line.png"
        await harvest_screenshot(engine, shot_dir, ref_png)
        log.info("reference (no-line): %s", ref_png.name)

        for angle in TEST_ANGLES_DEG:
            log.info("spawning aim line at %.0f deg", angle)
            await engine.python_exec(assemble_aim_line(workspace, angle, length_cm))
            await asyncio.sleep(0.5)
            png = workspace / f"aim_{int(angle):+04d}_deg.png"
            got = await harvest_screenshot(engine, shot_dir, png)
            if got:
                log.info("  saved %s", png.name)
            else:
                log.warning("  screenshot failed for angle=%.0f", angle)
            await engine.python_exec(assemble_clear_aim_line())
            await asyncio.sleep(0.3)

        log.info("done — %d screenshots in %s", len(TEST_ANGLES_DEG) + 1, workspace)
    finally:
        try:
            await engine.stop()
        except Exception:
            pass


def _main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", type=Path, required=True,
                   help="Path to demo1.exe (used to locate the screenshot dir)")
    p.add_argument("--port", type=int, default=9003)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--difficulty", choices=["standard", "hard"],
                   default="standard")
    p.add_argument("--length", type=float, default=1500.0,
                   help="aim-line length in cm (1500 covers hard diagonal)")
    p.add_argument("--out-root", type=Path,
                   default=Path("_aim_line_test"))
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(name)s [%(levelname)s] %(message)s")

    asyncio.run(_amain(
        exe=args.exe, port=args.port, seed=args.seed,
        difficulty=args.difficulty, length_cm=args.length,
        out_root=args.out_root))


if __name__ == "__main__":
    _main()
