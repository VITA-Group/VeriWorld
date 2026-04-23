"""Shared building blocks for Tunnel ablations.

Each ablation (``vp_bf/``, ``af/``, …) is a self-contained folder with
its own ``task.py``. This module contains only stateless utilities — no
episode-level branching.

What lives here:

* UE-side code snippets (``LEVEL_SWITCH_CODE``, ``RAYCAST_CODE_3D``, …).
  The raycast is 12-direction (8 horizontal + up/down + ±30° pitch) to
  resolve the curved 3D tunnel geometry.
* Response parsers (``extract_moves_batch``, ``extract_moves_aim_and_fly``).
* :class:`PositionTracker` and :func:`yaw_to_dir`.
* :func:`take_screenshot` — file-based retrieval.
* Stdout helpers (``unwrap``, ``parse_raycast``, ``parse_navlog``).
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent
# Task-level (shared across harnesses): ue_setup.py, move_camera.py,
# generate_params.py live at the parent of this harness folder.
TASK_ROOT = HERE.parent


# ---------------------------------------------------------------------------
# UE-side snippets
# ---------------------------------------------------------------------------
LEVEL_SWITCH_CODE = (
    'import unreal_runtime as ur\n'
    'current = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
    'if current != "Untitled":\n'
    '    ur.Engine.GameplayStatics.OpenLevel("Untitled", True, "")\n'
    '    print("LEVEL_SWITCHING")\n'
    'else:\n'
    '    print("LEVEL_READY")\n'
)

CLEAN_VOXEL_CODE = (
    'import unreal_runtime as ur\n'
    'world = ur.Engine.GetDefaultWorld()\n'
    'count = 0\n'
    'for a in world.GetActorsOfClass(ur.Voxel.VoxelStampActor):\n'
    '    a.K2_DestroyActor()\n'
    '    count += 1\n'
    'print(f"[clean] Destroyed {count} VoxelStampActors")\n'
)

RAYCAST_CODE_3D = '''\
import unreal_runtime as ur, math
gm = ur.Engine.GetGameMode()
world = ur.Engine.GetDefaultWorld()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if pawns:
    pawn = pawns[0]
    loc = pawn.K2_GetActorLocation()
    ctrl = pawn.GetController()
    yaw = ctrl.GetControlRotation().Yaw
    ksm = ur.Engine.KismetSystemLibrary
    results = []
    scans = [
        ("Front", yaw, 0), ("FrontRight", yaw-45, 0), ("Right", yaw-90, 0),
        ("BackRight", yaw-135, 0), ("Back", yaw+180, 0), ("BackLeft", yaw+135, 0),
        ("Left", yaw+90, 0), ("FrontLeft", yaw+45, 0),
        ("Up", yaw, 90), ("Down", yaw, -90),
        ("FrontUp", yaw, 30), ("FrontDown", yaw, -30),
    ]
    for name, scan_yaw, scan_pitch in scans:
        yaw_rad = math.radians(scan_yaw)
        pitch_rad = math.radians(scan_pitch)
        cos_p = math.cos(pitch_rad)
        start = ur.CoreUObject.Vector(); start.X, start.Y, start.Z = loc.X, loc.Y, loc.Z
        end = ur.CoreUObject.Vector()
        end.X = loc.X + math.cos(yaw_rad) * cos_p * 1000
        end.Y = loc.Y + math.sin(yaw_rad) * cos_p * 1000
        end.Z = loc.Z + math.sin(pitch_rad) * 1000
        hit = ur.Engine.HitResult()
        tc = ur.CoreUObject.LinearColor(); tc.R=1;tc.G=0;tc.B=0;tc.A=1
        hc = ur.CoreUObject.LinearColor(); hc.R=0;hc.G=1;hc.B=0;hc.A=1
        got_hit = ksm.LineTraceSingle(start, end, ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
            False, [], ur.Engine.EDrawDebugTrace.ForOneFrame, hit, True, tc, hc, 1.0)
        if got_hit and hit.bBlockingHit:
            results.append(f"{name}(yaw={scan_yaw:.0f},pitch={scan_pitch:.0f}): wall at {hit.Distance:.0f}cm")
        else:
            results.append(f"{name}(yaw={scan_yaw:.0f},pitch={scan_pitch:.0f}): open (>1000cm)")
    print("RAYCAST:" + "|".join(results))
'''

MOVE_CAMERA_CODE = (TASK_ROOT / "move_camera.py").read_text(encoding="utf-8")
_SETUP_TEMPLATE = (TASK_ROOT / "ue_setup.py").read_text(encoding="utf-8")


def build_setup_code(params: dict, tunnel_radius: float, colorful: bool) -> str:
    """Inject params + tunnel knobs into the UE-side setup script."""
    return (
        f"import builtins, json; "
        f"builtins._MAZE_PARAMS = json.loads(r'''{json.dumps(params)}'''); "
        f"builtins._TUNNEL_RADIUS = {tunnel_radius}; "
        f"builtins._TUNNEL_COLORFUL = {colorful}; "
        f"builtins._TUNNEL_CEILING = 'voxel'\n"
        + _SETUP_TEMPLATE
    )


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------
def extract_moves_batch(text: str) -> list:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))["moves"]
        except Exception:
            pass
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return []


def extract_moves_aim_and_fly(text: str) -> list:
    """AF compound: {"see":"...", "yaw":N, "pitch":M, "forward":D} →
    decompose into separate turn / move_z / forward commands."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r'\{[^{}]*"yaw"[^{}]*\}', text)
    if not m:
        return [{"cmd": "forward", "distance": 100}]
    try:
        data = json.loads(m.group(1) if "```" in m.group(0) else m.group(0))
        yaw_deg = float(data.get("yaw", data.get("turn", 0)))
        pitch_deg = float(data.get("pitch", 0))
        fwd_dist = float(data.get("forward", 0))
        moves: list = []
        if abs(yaw_deg) > 0.5:
            moves.append({"cmd": "turn", "degrees": yaw_deg})
        if fwd_dist > 0:
            pitch_rad = math.radians(pitch_deg)
            horiz = fwd_dist * math.cos(pitch_rad)
            vert = fwd_dist * math.sin(pitch_rad)
            if abs(vert) > 1:
                moves.append({"cmd": "move_z", "distance": vert})
            moves.append({"cmd": "forward", "distance": horiz})
        elif fwd_dist < 0:
            moves.append({"cmd": "backward", "distance": abs(fwd_dist)})
        return moves or [{"cmd": "forward", "distance": 100}]
    except Exception:
        return [{"cmd": "forward", "distance": 100}]


def extract_thought(text: str) -> str:
    return text.split("```", 1)[0].strip()


# ---------------------------------------------------------------------------
# Position tracker
# ---------------------------------------------------------------------------
def yaw_to_dir(yaw: float) -> str:
    y = round(yaw) % 360
    if y < 45 or y >= 315:
        return "East"
    if 45 <= y < 135:
        return "North"
    if 135 <= y < 225:
        return "West"
    return "South"


class PositionTracker:
    def __init__(self, use_cardinal: bool = True):
        self.positions: dict = defaultdict(dict)
        self.visit_order: list = []
        self.use_cardinal = use_cardinal

    def update(self, entries: list) -> None:
        for e in entries:
            if not isinstance(e, dict):
                continue
            from_x = round(float(e.get("from_x", 0)))
            from_y = round(float(e.get("from_y", 0)))
            to_x = round(float(e.get("to_x", e.get("x", 0))))
            to_y = round(float(e.get("to_y", e.get("y", 0))))
            yaw = float(e.get("yaw", 0))
            blocked = e.get("blocked", False)
            cmd = e.get("cmd", "")
            from_key = (from_x, from_y)
            if from_key not in self.visit_order:
                self.visit_order.append(from_key)
            if cmd in ("forward", "backward") or cmd.startswith(("forward(", "backward(")):
                status = "BLOCKED" if blocked else "OPEN"
                direction = yaw_to_dir(yaw)
                if cmd.startswith("backward"):
                    rev = {"East": "West", "West": "East", "North": "South", "South": "North"}
                    direction = rev[direction]
                self.positions[from_key][direction] = status
            to_key = (to_x, to_y)
            if to_key not in self.visit_order:
                self.visit_order.append(to_key)

    def format_map(self, cur_x: float, cur_y: float, cur_yaw: float) -> str:
        lines = ["EXPLORED POSITIONS:", ""]
        for pos in self.visit_order:
            dirs = self.positions.get(pos, {})
            here = " <-- YOU ARE HERE" if pos == (round(cur_x), round(cur_y)) else ""
            if dirs:
                dir_strs = [f"{d}={s}" for d, s in sorted(dirs.items())]
                lines.append(f"  ({pos[0]}, {pos[1]}): {', '.join(dir_strs)}{here}")
            else:
                lines.append(f"  ({pos[0]}, {pos[1]}): (no forward attempts here){here}")
        lines.append("")
        lines.append(f"Total positions visited: {len(self.visit_order)}")
        if self.use_cardinal:
            lines.append(f"Current facing: {yaw_to_dir(cur_yaw)} (yaw={cur_yaw:.0f})")
        else:
            lines.append(f"Current facing: yaw={cur_yaw:.0f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Screenshot + stdout helpers
# ---------------------------------------------------------------------------
async def take_screenshot(engine, name: str, screenshot_dir: Path, workspace: Path) -> Optional[Path]:
    await engine.python_exec(f'import unreal_runtime as ur; ur.TakeScreenshot("{name}")')
    await asyncio.sleep(0.5)
    matches = list(screenshot_dir.glob(f"*{name}*"))
    if not matches:
        return None
    src = max(matches, key=lambda p: p.stat().st_mtime)
    dst = workspace / f"{name}.png"
    shutil.copy2(src, dst)
    return dst


def unwrap(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


def parse_raycast(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("RAYCAST:"):
            return "\n".join(f"  {item.strip()}" for item in line[8:].split("|"))
    return "  (raycast failed)"


def parse_navlog(stdout: str) -> list:
    for line in stdout.splitlines():
        if line.startswith("NAVLOG:"):
            try:
                data = json.loads(line[7:])
                return data if isinstance(data, list) else [data]
            except Exception:
                return []
    return []


__all__ = [
    "HERE",
    "LEVEL_SWITCH_CODE", "CLEAN_VOXEL_CODE", "RAYCAST_CODE_3D", "MOVE_CAMERA_CODE",
    "build_setup_code",
    "extract_moves_batch", "extract_moves_aim_and_fly", "extract_thought",
    "yaw_to_dir", "PositionTracker",
    "take_screenshot", "unwrap", "parse_raycast", "parse_navlog",
]
