"""Frame-grid helper for the moving_shooter *_frames harnesses.

Converts a round-replay mp4 into a single 2x3 grid PNG (six frames
sampled evenly across the clip, time-ordered left-to-right then
top-to-bottom). This gives every model an identical visual channel
through the ordinary image-chat path — the original harness_vision
attaches the raw video, which only Gemini-family transports accept
(all other models silently degrade to text-only, see
VLMClient.chat_with_video). Uses the same LightWar-style frame-stack
idea documented in the paper (Fig. 4).

Requires ffmpeg/ffprobe on PATH and Pillow (via common.screenshot.make_grid).
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from veriworld.common.screenshot import make_grid

log = logging.getLogger(__name__)

class FrameTransportError(RuntimeError):
    """Required visual feedback was unavailable; no model score may be reported."""


N_FRAMES = 6
GRID_COLS = 3
TILE_SIZE = (640, 480)


def _duration_s(video: Path) -> Optional[float]:
    """Container duration with fallbacks — UE's recorder emits mp4s
    whose format/stream duration can be 'N/A', in which case we decode
    the stream once to count frames and derive duration from the frame
    rate."""
    def _probe(entries: str) -> str:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", entries,
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=120)
        return out.stdout.strip()
    try:
        # Guard first: is this actually a container, or a raw elementary
        # stream wearing an .mp4 filename?
        #
        # UE's recorder writes bare Annex-B h264. Renamed to .mp4 it
        # still probes "successfully", but every timing answer is wrong:
        # both duration fields come back N/A, and the r_frame_rate
        # fallback reports 60/1 for a 30 fps capture — the h264 tick
        # rate, which is double the frame rate. 260 frames / 60 then
        # yields 4.333 s for an 8.667 s clip, so the six sampled frames
        # land in the first half and the whole back end of the round
        # (typically the impact) is invisible. That is not detectable by
        # cross-checking frame counts, because both paths inherit the
        # same bogus rate. The only reliable signal is the container
        # format itself.
        fmt = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        if fmt and not any(k in fmt for k in
                           ("mp4", "mov", "matroska", "webm", "avi")):
            log.error(
                "%s is a raw %r elementary stream, not a container — its "
                "timing cannot be trusted and frame sampling would silently "
                "cover only part of the clip. Remux it first "
                "(ffmpeg -i in.h264 -c:v copy out.mp4); see "
                "_shared._remux_to_mp4.", video.name, fmt)
            return None

        for entries in ("format=duration", "stream=duration"):
            val = _probe(entries).splitlines()[0] if _probe(entries) else ""
            try:
                return float(val)
            except ValueError:
                continue
        raw = _probe("stream=r_frame_rate,nb_read_frames").splitlines()
        raw = [x for x in raw if x and x != "N/A"]
        fps = None
        nframes = None
        for x in raw:
            if "/" in x:
                num, den = x.split("/", 1)
                if float(den):
                    fps = float(num) / float(den)
            elif x.isdigit():
                nframes = int(x)
        if nframes is None:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=nb_read_frames",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                capture_output=True, text=True, timeout=120)
            s = out.stdout.strip()
            nframes = int(s) if s.isdigit() else None
        if fps and nframes:
            return nframes / fps
    except Exception as e:  # noqa: BLE001
        log.warning("ffprobe failed on %s: %s", video, e)
    return None


def video_to_grid(video: Path, out_png: Path,
                  n_frames: int = N_FRAMES) -> Optional[Path]:
    """Extract n_frames evenly spaced frames and stitch a grid PNG.

    Returns out_png on success, None on extraction failure. The evaluated
    frame-grid callers abort the episode if a required grid is unavailable."""
    dur = _duration_s(video)
    if not dur or dur <= 0:
        return None
    tiles: list[Path] = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(n_frames):
            t = (i + 0.5) * dur / n_frames
            tile = Path(td) / f"f{i:02d}.png"
            try:
                # -ss AFTER -i: accurate decode-seek that does not
                # depend on container seek index (UE mp4s lack one).
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error",
                     "-i", str(video), "-ss", f"{t:.3f}",
                     "-frames:v", "1", str(tile)],
                    capture_output=True, timeout=120, check=True)
            except Exception as e:  # noqa: BLE001
                log.warning("ffmpeg frame %d failed on %s: %s", i, video, e)
                return None
            if not tile.exists():
                return None
            tiles.append(tile)
        try:
            # Native 640x480 tiles, not make_grid's 320x240 default:
            # the target sphere is only a few pixels across at half
            # resolution. LightWar's frame-stack (the precedent this
            # channel is modelled on) also composites at native size.
            grid = make_grid(tiles, cols=GRID_COLS, tile_size=TILE_SIZE)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            grid.save(out_png)
        except Exception as e:  # noqa: BLE001
            log.warning("make_grid failed for %s: %s", video, e)
            return None
    return out_png


__all__ = ["video_to_grid", "N_FRAMES", "GRID_COLS", "TILE_SIZE"]
