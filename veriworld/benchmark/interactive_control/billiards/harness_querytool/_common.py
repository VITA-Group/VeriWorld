"""Image-modality helpers reused from harness_image / harness_visiongroundtruth.

Same screenshot harvest pattern: trigger UE-side ``engine.screenshot()``,
poll the build's ``Saved/Screenshots/Windows`` dir for the newest png,
copy into the workspace.
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import time
from pathlib import Path
from typing import Optional


def screenshot_dir_from_exe(exe: Path) -> Path:
    return Path(exe).parent / "demo1" / "Saved" / "Screenshots" / "Windows"


def latest_screenshot(shot_dir: Path, since: float) -> Optional[Path]:
    if not shot_dir.exists(): return None
    cands = [p for p in shot_dir.glob("*.png") if p.stat().st_mtime > since]
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


async def harvest_screenshot(engine, shot_dir: Path, dest_png: Path) -> Path:
    """Capture this runtime's fresh frame; ignore the legacy shared directory."""
    from veriworld.common.screenshot import capture_runtime_screenshot
    return await capture_runtime_screenshot(engine, dest_png.stem, dest_png.parent)


def png_to_data_url(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


__all__ = [
    "screenshot_dir_from_exe", "latest_screenshot",
    "harvest_screenshot", "png_to_data_url",
]
