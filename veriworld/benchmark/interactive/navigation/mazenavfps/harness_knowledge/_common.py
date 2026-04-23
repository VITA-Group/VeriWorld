"""Shared building blocks for MazeNavFPS ablations.

Each ablation (``vp_bf/``, ``pv_bf/``, etc.) is a self-contained folder
with its own ``task.py`` that imports whatever it needs from here. This
module deliberately contains **no** episode-level control flow — no
condition branching, no prompt building. It's just a toolbox.

What lives here:

* UE-side code snippets (``LEVEL_SWITCH_CODE``, ``RAYCAST_CODE``, …).
* Response parsers (``extract_moves_batch``, ``extract_moves_single_free``, …).
* :class:`PositionTracker` and :func:`yaw_to_dir`.
* :func:`make_history_grid` (for the purevision variant).
* Small stdout helpers (``unwrap``, ``parse_raycast``, ``parse_navlog``).
* :func:`take_screenshot` — file-based screenshot retrieval.

Per-ablation ``task.py`` files hard-code their condition booleans (is
there a screenshot? is there raycast? batch or single?) and drive the
episode loop directly against these helpers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import shutil
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import List, Optional

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

RAYCAST_CODE = (
    'import unreal_runtime as ur, math\n'
    'gm = ur.Engine.GetGameMode()\n'
    'world = ur.Engine.GetDefaultWorld()\n'
    'pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n'
    'if pawns:\n'
    '    pawn = pawns[0]\n'
    '    loc = pawn.K2_GetActorLocation()\n'
    '    ctrl = pawn.GetController()\n'
    '    yaw = ctrl.GetControlRotation().Yaw\n'
    '    ksm = ur.Engine.KismetSystemLibrary\n'
    '    results = []\n'
    '    for name, angle in [("Front", yaw), ("Right", yaw-90), ("Back", yaw+180), ("Left", yaw+90)]:\n'
    '        rad = math.radians(angle)\n'
    '        start = ur.CoreUObject.Vector(); start.X, start.Y, start.Z = loc.X, loc.Y, loc.Z\n'
    '        end = ur.CoreUObject.Vector()\n'
    '        end.X = loc.X + math.cos(rad)*1000; end.Y = loc.Y + math.sin(rad)*1000; end.Z = loc.Z\n'
    '        hit = ur.Engine.HitResult()\n'
    '        tc = ur.CoreUObject.LinearColor(); tc.R=1;tc.G=0;tc.B=0;tc.A=1\n'
    '        hc = ur.CoreUObject.LinearColor(); hc.R=0;hc.G=1;hc.B=0;hc.A=1\n'
    '        got_hit = ksm.LineTraceSingle(start, end, ur.Engine.ETraceTypeQuery.TraceTypeQuery1,\n'
    '            False, [], ur.Engine.EDrawDebugTrace.ForOneFrame, hit, True, tc, hc, 1.0)\n'
    '        if got_hit and hit.bBlockingHit:\n'
    '            results.append(f"{name}: wall at {hit.Distance:.0f}cm")\n'
    '        else:\n'
    '            results.append(f"{name}: open (>1000cm)")\n'
    '    print("RAYCAST:" + "|".join(results))\n'
)

MOVE_CAMERA_CODE = (TASK_ROOT / "move_camera.py").read_text(encoding="utf-8")
_SETUP_TEMPLATE = (TASK_ROOT / "ue_setup.py").read_text(encoding="utf-8")


def build_setup_code(params: dict, materials: int) -> str:
    """Inject params + materials into the UE-side setup script."""
    return (
        f"import builtins, json; "
        f"builtins._MAZE_PARAMS = json.loads(r'''{json.dumps(params)}'''); "
        f"builtins._MAZE_MATERIALS = {materials}\n"
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
    m = re.search(r'\{[^{}]*"moves"\s*:\s*\[.*?\][^{}]*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))["moves"]
        except Exception:
            pass
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return []


def extract_moves_single_free(text: str) -> list:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if "cmd" in data:
                return [data]
            if "move" in data and isinstance(data["move"], dict):
                return [data["move"]]
            if "moves" in data and isinstance(data["moves"], list) and data["moves"]:
                first = data["moves"][0]
                if isinstance(first, dict):
                    return [first]
        except Exception:
            pass
    return []


def extract_moves_single_fixed(text: str) -> list:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if "move" in data:
                return [data["move"]]
        except Exception:
            pass
    m = re.search(r'"move"\s*:\s*"(forward|turn_left|turn_right)"', text)
    if m:
        return [m.group(1)]
    return []


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
        self.positions_yaw: dict = defaultdict(dict)
        self.visit_order: list = []
        self.use_cardinal = use_cardinal

    def update(self, log_entries: list) -> None:
        for e in log_entries:
            if not isinstance(e, dict):
                continue
            cmd = e.get("cmd", "")
            from_x = round(float(e.get("from_x", 0)))
            from_y = round(float(e.get("from_y", 0)))
            to_x = round(float(e.get("to_x", e.get("x", 0))))
            to_y = round(float(e.get("to_y", e.get("y", 0))))
            yaw = float(e.get("yaw", 0))
            blocked = e.get("blocked", False)
            from_key = (from_x, from_y)
            to_key = (to_x, to_y)
            if from_key not in self.visit_order:
                self.visit_order.append(from_key)
            if cmd in ("forward", "backward") or cmd.startswith(("forward(", "backward(")):
                status = "BLOCKED" if blocked else "OPEN"
                direction = yaw_to_dir(yaw)
                if cmd == "backward" or cmd.startswith("backward("):
                    rev = {"East": "West", "West": "East", "North": "South", "South": "North"}
                    direction = rev[direction]
                self.positions[from_key][direction] = status
                self.positions_yaw[from_key][f"yaw{round(yaw) % 360}"] = status
            if to_key not in self.visit_order:
                self.visit_order.append(to_key)

    def format_map(self, cur_x: float, cur_y: float, cur_yaw: float) -> str:
        lines = ["EXPLORED POSITIONS:", ""]
        pos_data = self.positions if self.use_cardinal else self.positions_yaw
        for pos in self.visit_order:
            dirs = pos_data.get(pos, {})
            here = " <-- YOU ARE HERE" if pos == (round(cur_x), round(cur_y)) else ""
            if dirs:
                dir_strs = [f"{d}={s}" for d, s in sorted(dirs.items())]
                lines.append(f"  ({pos[0]}, {pos[1]}): {', '.join(dir_strs)}{here}")
            else:
                lines.append(f"  ({pos[0]}, {pos[1]}): (no forward attempts here){here}")
        lines.append("")
        counts: dict[str, int] = {}
        for dirs in pos_data.values():
            for s in dirs.values():
                counts[s] = counts.get(s, 0) + 1
        lines.append(f"Total positions visited: {len(self.visit_order)}")
        lines.append(f"Walls found (BLOCKED): {counts.get('BLOCKED', 0)}")
        lines.append(f"Open passages (OPEN): {counts.get('OPEN', 0)}")
        if self.use_cardinal:
            lines.append(f"Current facing: {yaw_to_dir(cur_yaw)} (yaw={cur_yaw:.0f})")
        else:
            lines.append(f"Current facing: yaw={cur_yaw:.0f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pure-vision history grid (used only by the PV ablation)
# ---------------------------------------------------------------------------
def make_history_grid(paths: List[Path], current_step: int) -> Optional[str]:
    """Return a labelled grid of the last N screenshots as a data URL."""
    from PIL import Image, ImageDraw  # local import

    paths = [p for p in paths if p and Path(p).exists()]
    if not paths:
        return None

    imgs = [Image.open(p).resize((320, 240), Image.LANCZOS) for p in paths]
    labels = [f"Step {current_step - len(paths) + i}" for i in range(len(paths))]

    if len(imgs) == 1:
        grid = imgs[0]
    else:
        cols = 2
        rows = (len(imgs) + cols - 1) // cols
        w, h = imgs[0].size
        pad = 25
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


# ---------------------------------------------------------------------------
# Screenshot helper (async, requires an :class:`InteractiveEngine`)
# ---------------------------------------------------------------------------
async def take_screenshot(engine, name: str, screenshot_dir: Path, workspace: Path) -> Optional[Path]:
    """Trigger a screenshot and copy the newest matching PNG into ``workspace``."""
    await engine.python_exec(f'import unreal_runtime as ur; ur.TakeScreenshot("{name}")')
    await asyncio.sleep(0.5)
    matches = list(screenshot_dir.glob(f"*{name}*"))
    if not matches:
        return None
    src = max(matches, key=lambda p: p.stat().st_mtime)
    dst = workspace / f"{name}.png"
    shutil.copy2(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Stdout helpers
# ---------------------------------------------------------------------------
def unwrap(resp: dict) -> str:
    """python_exec envelope → stdout string."""
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


# ---------------------------------------------------------------------------
# KnowledgeManager — the defining primitive of this harness
# ---------------------------------------------------------------------------
class KnowledgeManager:
    """Append-only narrative log of the agent's own thoughts + moves +
    results, fed back into the prompt each step so the agent can
    self-reflect on its prior reasoning.

    Ported from
    ``AxisWorld-benchmark/unreal_projects_lean/veriworld/harness_win/
    parallel_harness.py::KnowledgeManager``. Deterministic — the
    harness just concatenates agent outputs, no extra LLM call.

    Contrast with ``PositionTracker``: that one records structured
    *world facts* (which cell is BLOCKED/OPEN) extracted from the UE
    movement log; this one records *the agent's own narrative*
    (thought in its own words, moves it decided on, results it then
    observed). Sibling harnesses differ along the axis "what kind of
    memory does the agent see?" — structured world-state vs.
    self-narrative.
    """

    def __init__(self, workspace: Path, show_coords: bool = True) -> None:
        self.path = workspace / "knowledge.md"
        self.entries: List[str] = []
        self.show_coords = show_coords

    def update(self, step: int, thought: str, moves: list, log_entries: list) -> None:
        entry = f"## Step {step}\n\n"
        entry += f"**Thought**: {thought}\n\n"
        entry += f"**Moves**: {json.dumps(moves)}\n\n"
        if log_entries:
            entry += "**Results**:\n"
            for e in log_entries:
                if not isinstance(e, dict):
                    continue
                status = "BLOCKED" if e.get("blocked") else "ok"
                if self.show_coords:
                    entry += (
                        f"- {e['cmd']}: pos=({e.get('to_x', '?')}, "
                        f"{e.get('to_y', '?')}) yaw={e.get('yaw', '?')} {status}\n"
                    )
                else:
                    entry += f"- {e['cmd']}: {status}\n"
            entry += "\n"
        self.entries.append(entry)
        self._write()

    def get_text(self) -> str:
        if not self.entries:
            return "(No observations yet.)"
        return "\n".join(self.entries)

    def _write(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# Navigation Knowledge\n\n")
            for e in self.entries:
                f.write(e)


__all__ = [
    "HERE",
    "LEVEL_SWITCH_CODE", "CLEAN_VOXEL_CODE", "RAYCAST_CODE", "MOVE_CAMERA_CODE",
    "build_setup_code",
    "extract_moves_batch", "extract_moves_single_free", "extract_moves_single_fixed",
    "extract_thought",
    "yaw_to_dir", "PositionTracker",
    "KnowledgeManager",
    "make_history_grid",
    "take_screenshot",
    "unwrap", "parse_raycast", "parse_navlog",
]
