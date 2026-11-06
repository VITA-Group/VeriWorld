"""Helpers for dealing with UE-side screenshot files.

UE saves screenshots to ``%LOCALAPPDATA%/<project>/Saved/Screenshots/Windows/``
by default. Tasks commonly need to:

* locate the most recent screenshot,
* encode it as base64 for a VLM request,
* stitch several screenshots into a grid (history / video-frame grids).

These are pure utilities — no UE state is touched. The engine-side
screenshot command is invoked through :class:`veriworld.common.ws.UEClient`.
"""

from __future__ import annotations

import base64
import asyncio
import glob
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


async def capture_runtime_screenshot(engine, name: str, workspace: Path,
                                     *, screenshot_dir: Optional[Path] = None,
                                     timeout: float = 15.0) -> Path:
    """Capture this instance's fresh frame on Windows or Linux.

    Unique names prevent parallel workers from consuming another worker's
    image. Missing images abort the episode before any visual model call.
    """
    from PIL import Image

    def output(response):
        value = response
        for _ in range(8):
            if isinstance(value, dict):
                for key in ('output', 'stdout', 'result'):
                    if key in value:
                        value = value[key]
                        break
                else:
                    return str(value)
            elif isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    return value
            else:
                return str(value)
        return str(value)

    marker = 'VW_SCREENSHOT_ROOT '
    reply = await engine.python_exec(
        "import unreal_runtime as ur, os\n"
        "print(" + repr(marker) + "+os.path.abspath(str(ur.Engine.KismetSystemLibrary.GetProjectSavedDirectory())))")
    roots = [line.split(marker, 1)[1].strip() for line in output(reply).splitlines() if marker in line]
    if not roots:
        raise RuntimeError('Could not resolve the running engine screenshot directory')
    root = Path(screenshot_dir) if screenshot_dir is not None else Path(roots[-1])/'Screenshots'
    token = 'vw_' + uuid.uuid4().hex
    started = time.time()
    await engine.python_exec('import unreal_runtime as ur\nur.TakeScreenshot(' + repr(token) + ')')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [p for p in root.rglob('*' + token + '*.png')
                      if p.stat().st_mtime >= started - 1]
        for source in candidates:
            try:
                with Image.open(source) as im:
                    im.verify()
                workspace.mkdir(parents=True, exist_ok=True)
                destination = workspace/(name + '.png')
                shutil.copy2(source, destination)
                return destination
            except (OSError, SyntaxError):
                continue
        await asyncio.sleep(0.1)
    raise TimeoutError('No fresh screenshot from this runtime: ' + token)


def default_screenshot_dir(project: str = "demo1") -> Path:
    """Return the default UE screenshot output directory on Windows."""
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / project / "Saved" / "Screenshots" / "Windows"


def wait_for_new_screenshot(
    folder: Path,
    *,
    seen: Optional[set[str]] = None,
    timeout: float = 10.0,
    poll: float = 0.1,
) -> Path:
    """Block until a new ``.png`` file appears in ``folder``.

    ``seen`` should contain the filenames already observed before the
    triggering command was issued. Returns the path of the new file.

    Raises :class:`TimeoutError` if nothing new appears within ``timeout``.
    """
    seen = set(seen or ())
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for p in folder.glob("*.png"):
            if p.name not in seen:
                return p
        time.sleep(poll)
    raise TimeoutError(f"no new screenshot in {folder} after {timeout}s")


def png_to_base64_url(path: Path | str) -> str:
    """Encode a PNG as a ``data:image/png;base64,...`` URL, ready for
    OpenAI-style ``image_url`` content blocks."""
    data = Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def make_grid(
    images: Iterable[Path | str],
    cols: int = 3,
    tile_size: Tuple[int, int] = (320, 240),
) -> "PILImage":  # noqa: F821
    """Combine PNGs into a single grid image. Requires Pillow.

    Used by the video-frame-grid feedback pattern (billiards) and the
    screenshot-history condition (mazenavfps purevision variant).
    """
    from PIL import Image  # local import so Pillow is optional at module load

    paths: List[Path] = [Path(p) for p in images]
    if not paths:
        raise ValueError("make_grid: no images")

    rows = (len(paths) + cols - 1) // cols
    tw, th = tile_size
    grid = Image.new("RGB", (tw * cols, th * rows), color=(0, 0, 0))
    for i, p in enumerate(paths):
        tile = Image.open(p).convert("RGB").resize(tile_size)
        r, c = divmod(i, cols)
        grid.paste(tile, (c * tw, r * th))
    return grid


__all__ = [
    "default_screenshot_dir",
    "wait_for_new_screenshot",
    "png_to_base64_url",
    "make_grid",
]
