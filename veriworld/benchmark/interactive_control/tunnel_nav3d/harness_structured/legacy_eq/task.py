# Legacy-faithful TunnelNav3D arms (AF / Bf / Sf x VP / VRP) with an equal primitive-move budget.
# Deviations from the paper's legacy harness are listed in this folder's README.md.
"""TunnelNav3D legacy arms on the release scaffold, plus an equal primitive-move budget.

Agent side (prompts, parsers, AF decomposition, position map, accumulated knowledge,
per-turn user message) follows the paper's legacy harness:
``VeriWorld-benchmark/unreal_projects_lean/veriworld/harness_win_tunnel/parallel_harness.py``.

Engine side (level switch, voxel clean, tunnel spawn, scene-ready check, screenshots,
raycast snippet, NAVLOG I/O) uses the release package
``veriworld.benchmark.interactive_control.tunnel_nav3d``. The movement tick task is the
release ``move_camera.py`` with only the batch-logging fix (``move_camera_fixed.py``).

The only prompt changes: the step header line, identical for all protocols,
``**Step k** (R of K moves remaining)``, and one appended system-prompt sentence
(``BUDGET_SENTENCE``).

Everything above ``run()`` is pure Python and importable without the engine or the
veriworld package, so it can be unit-tested offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
MOVE_CAMERA_FIXED_PATH = HERE / "move_camera_fixed.py"

log = logging.getLogger(__name__)

PROTOCOLS = ("AF", "Bf", "Sf")
INFOS = ("VP", "VRP")

# ═══════════════════════════════════════════════════════════
#  Legacy prompt text (parallel_harness.py L637-760), verbatim
# ═══════════════════════════════════════════════════════════

AF_ACTION_DESC = (
    "## AIM AND FLY\n"
    "Each turn you AIM the camera, then FLY toward where you're looking.\n"
    "```json\n"
    '{"see": "hole upper-right of center", "yaw": 20, "pitch": -15, "forward": 150}\n'
    "```\n"
    "- **see** (REQUIRED): What do you see? Where is the hole in the image?\n"
    "- **yaw**: degrees to turn horizontally. Positive=right, Negative=left.\n"
    "- **pitch**: degrees to tilt camera. Positive=look up, Negative=look down.\n"
    "  Hole in upper part of image -> pitch positive. Lower part -> pitch negative.\n"
    "- **forward**: cm to fly toward where you're aiming. 0=just aim. Negative=back up.\n"
    "  The camera moves in 3D direction it's pointing (including up/down).\n\n"
    "## STRATEGY\n"
    "1. HOLE VISIBLE -> aim (yaw+pitch) at center -> forward 150 to fly through.\n"
    "2. NO HOLE (wall ahead) -> try IN ORDER:\n"
    "   a) BACK UP (forward=-150). Gives wider view.\n"
    "   b) TURN sideways (yaw=60 or yaw=-60).\n"
    "   c) Look UP (pitch=20) or DOWN (pitch=-20) -- tunnel goes vertically!\n"
    "   d) BIG turns (yaw=90, 180) to find new wall.\n"
    "3. BLOCKED (hit wall) -> forward=-150 to back up.\n"
)

BF_ACTION_DESC = (
    "Send a BATCH of free movement commands each turn.\n"
    "```json\n"
    '{"moves": [{"cmd": "forward", "distance": 300}, '
    '{"cmd": "turn", "degrees": 45}, '
    '{"cmd": "move_z", "distance": 50}, '
    '{"cmd": "forward", "distance": 200}]}\n'
    "```\n"
    "Commands:\n"
    '- {"cmd": "forward", "distance": D} -- move forward D cm. Stops at walls.\n'
    '- {"cmd": "backward", "distance": D} -- move backward D cm.\n'
    '- {"cmd": "turn", "degrees": A} -- turn horizontally. Positive=right, Negative=left. Any angle.\n'
    '- {"cmd": "move_z", "distance": D} -- move up (positive) or down (negative) in cm.\n'
    "Send 3-6 commands per batch. The tunnel curves in 3D.\n"
)

SF_ACTION_DESC = (
    "Send ONE free movement command per turn.\n"
    "```json\n"
    '{"cmd": "forward", "distance": 300}\n'
    "```\n"
    "Commands:\n"
    '- {"cmd": "forward", "distance": D} -- move forward D cm. Stops at walls.\n'
    '- {"cmd": "backward", "distance": D} -- move backward D cm.\n'
    '- {"cmd": "turn", "degrees": A} -- turn horizontally. Any angle.\n'
    '- {"cmd": "move_z", "distance": D} -- move up/down in cm.\n'
    "ONE command per turn.\n"
)

INFO_DESC = {
    "VP": (
        "Each turn you see a screenshot AND a list of explored positions with BLOCKED/OPEN info.\n"
        "Use both the screenshot and the coordinate log to navigate.\n"
    ),
    "VRP": (
        "Each turn you see a screenshot, a list of explored positions with BLOCKED/OPEN info,\n"
        "AND a raycast scan (wall distances in 4 directions).\n"
        "Use all three sources to navigate.\n"
    ),
}

# Legacy INFO_MAP (L489-494) restricted to the two arms in scope.
INFO_MAP = {
    "VP": {"screenshot": True, "position_history": True, "raycast": False},
    "VRP": {"screenshot": True, "position_history": True, "raycast": True},
}

# The one added system-prompt sentence (identical for AF, Bf, Sf).
BUDGET_SENTENCE = ("Every engine command (turn, move_z, forward, backward) uses one move "
                   "from a fixed total budget of {K} moves.")

USER_FOOTER = "Explore systematically. Avoid revisiting dead ends."

DEFAULT_MOVE = {
    "AF": [{"cmd": "forward", "distance": 100}],
    "Bf": [{"cmd": "forward", "distance": 200}],
    "Sf": [{"cmd": "forward", "distance": 200}],
}


def legacy_system_prompt(protocol: str, info: str) -> str:
    """Legacy system prompt for this protocol+info (L730-760), without the budget sentence."""
    if protocol == "AF":
        return (
            "You are a camera inside a 3D tunnel carved through colorful walls.\n"
            "Each turn you see a screenshot. You make ONE action per turn.\n"
            "You must follow the tunnel from the green marker to the red marker.\n\n"
            "## HOW TO READ THE SCREENSHOT\n"
            "- Center of image = straight ahead at your current pitch angle\n"
            "- Left/right = horizontal direction\n"
            "- Up/down in image = vertical direction (tunnel goes up and down!)\n"
            "- Dark HOLE or OPENING = tunnel path to follow\n\n"
            f"{AF_ACTION_DESC}\n"
            "## AVOID BACKTRACKING (CRITICAL)\n"
            "- Holes are ONLY in COLORED WALLS (teal, blue, yellow, pink, etc). NOT in white floor or ceiling.\n"
            "- In your \"see\" field, ALWAYS name the wall colors you see.\n"
            "- REMEMBER the color of the wall you just flew THROUGH. That hole is now BEHIND you.\n"
            "- If you see that SAME color wall with a hole, that is the way BACK. DO NOT enter it.\n"
        )
    action_desc = BF_ACTION_DESC if protocol == "Bf" else SF_ACTION_DESC
    return (
        "You are a camera flying inside a 3D tunnel carved through a grid of walls.\n"
        "Navigate from the green marker to the red marker by following the tunnel.\n"
        "The tunnel curves in 3D. You can fly in any direction.\n\n"
        f"{INFO_DESC[info]}\n"
        f"RESPONSE FORMAT:\nTHOUGHT (plain text), then JSON:\n{action_desc}\n"
        "IMPORTANT:\n"
        "- If blocked, the distance_moved tells you how far the wall was.\n"
        "- The tunnel curves — use turn and move_z to follow it.\n"
        "- When you see an opening, aim toward it.\n"
        "- No goal coordinates given. Follow the tunnel.\n"
    )


def build_system_prompt(protocol: str, info: str, move_budget: int) -> str:
    """Legacy system prompt + the single budget sentence as its final line."""
    return legacy_system_prompt(protocol, info) + BUDGET_SENTENCE.format(K=move_budget) + "\n"


def budget_header(step: int, used: int, move_budget: int) -> str:
    """Replaces legacy ``**Step {step}/{max_steps}** ({max_steps - step} steps remaining)``."""
    return f"**Step {step}** ({move_budget - used} of {move_budget} moves remaining)"


# ═══════════════════════════════════════════════════════════
#  Legacy parsers (L280-379), verbatim
# ═══════════════════════════════════════════════════════════

def extract_moves_batch(text):
    """Batch: {"moves": [...]} or bare [...]"""
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
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
    m = re.search(r'```json\s*(\[.*?\])\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r'(\[[\s\S]*?"cmd"[\s\S]*?\])', text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return []


def extract_moves_single_free(text):
    """Single free: {"cmd": "forward", "distance": 600}
    Fallback: if model returns batch {"moves": [...]}, take only the first command."""
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if "cmd" in data:
                return [data]
            if "move" in data and isinstance(data["move"], dict):
                return [data["move"]]
            # Fallback: model returned batch format, take first command
            if "moves" in data and isinstance(data["moves"], list) and len(data["moves"]) > 0:
                first = data["moves"][0]
                if isinstance(first, dict):
                    return [first]
        except Exception:
            pass
    return []


def extract_moves_aim_and_fly(text):
    """Aim-and-fly: {"see":"...", "yaw":20, "pitch":-15, "forward":150}
    Decompose into turn + move_z + forward."""
    import math as _m
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if not m:
        m = re.search(r'\{[^{}]*"yaw"[^{}]*\}', text)
    if m:
        try:
            data = json.loads(m.group(1) if '```' in m.group(0) else m.group(0))
            yaw_deg = float(data.get("yaw", data.get("turn", 0)))
            pitch_deg = float(data.get("pitch", 0))
            fwd_dist = float(data.get("forward", 0))
            moves = []
            if abs(yaw_deg) > 0.5:
                moves.append({"cmd": "turn", "degrees": yaw_deg})
            if fwd_dist > 0:
                pitch_rad = _m.radians(pitch_deg)
                horiz = fwd_dist * _m.cos(pitch_rad)
                vert = fwd_dist * _m.sin(pitch_rad)
                if abs(vert) > 1:
                    moves.append({"cmd": "move_z", "distance": vert})
                moves.append({"cmd": "forward", "distance": horiz})
            elif fwd_dist < 0:
                moves.append({"cmd": "backward", "distance": abs(fwd_dist)})
            if moves:
                return moves
        except Exception:
            pass
    return [{"cmd": "forward", "distance": 100}]


def extract_thought(text):
    return text.split("```", 1)[0].strip()


# ═══════════════════════════════════════════════════════════
#  Shorthand sanitizer (Bf / Sf) and budgeted move planning
# ═══════════════════════════════════════════════════════════

_STRING_CMDS = ("forward", "backward", "turn_left", "turn_right")  # executed by move_camera's string branch
_ARG = {"forward": "distance", "backward": "distance", "move_z": "distance", "turn": "degrees"}


def _finite_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def sanitize(moves):
    """Return (executable_items, changes).

    Keeps items move_camera executes (string commands; dicts whose ``cmd`` is
    forward/backward/turn/move_z with a finite numeric argument or none), maps
    single-key shorthand ``{"turn"|"yaw": x}`` -> turn and ``{"forward"|"backward"|
    "move_z": x}`` -> that command, and drops everything else. Each mapping/drop is
    recorded in ``changes``.
    """
    out, changes = [], []
    if not isinstance(moves, list):
        if moves not in (None, []):
            changes.append({"from": moves, "to": None, "reason": "not_a_list"})
        return out, changes
    for m in moves:
        if isinstance(m, str) and m in _STRING_CMDS:
            out.append(m)
            continue
        if isinstance(m, dict):
            c = m.get("cmd")
            if c in _ARG:
                arg = _ARG[c]
                if arg not in m or _finite_number(m[arg]):
                    out.append(m)
                    continue
                changes.append({"from": m, "to": None, "reason": "non_numeric_argument"})
                continue
            if c is None and len(m) == 1:
                (k, v), = m.items()
                if _finite_number(v):
                    if k in ("forward", "backward", "move_z"):
                        out.append({"cmd": k, "distance": v})
                        changes.append({"from": m, "to": out[-1], "reason": "shorthand"})
                        continue
                    if k in ("turn", "yaw"):
                        out.append({"cmd": "turn", "degrees": v})
                        changes.append({"from": m, "to": out[-1], "reason": "shorthand"})
                        continue
        changes.append({"from": m, "to": None, "reason": "not_executable"})
    return out, changes


def plan_moves(protocol: str, response: str, remaining: int) -> dict:
    """Legacy parse (+ sanitizer for Bf/Sf, + decomposition for AF), legacy fallback,
    then truncation to the remaining primitive-move budget.

    Returns a record with ``sent`` (exact engine command list), ``extracted``,
    ``changes`` (sanitizer edits; empty when nothing changed), ``fallback`` and
    ``truncated_from``.
    """
    if remaining < 1:
        raise ValueError("No moves remaining")
    changes: list = []
    if protocol == "AF":
        extracted = extract_moves_aim_and_fly(response)
        planned = extracted
        bad = [mv for mv in planned if not all(_finite_number(v) for k, v in mv.items() if k != "cmd")]
        if bad:  # json.loads accepts NaN/Infinity; the engine would never finish such a command
            changes.append({"from": planned, "to": None, "reason": "non_finite_af_value"})
            planned = []
    elif protocol == "Bf":
        extracted = extract_moves_batch(response)
        planned, changes = sanitize(extracted)
    elif protocol == "Sf":
        extracted = extract_moves_single_free(response)
        planned, changes = sanitize(extracted)
    else:
        raise ValueError(f"Unknown protocol {protocol!r}")
    fallback = not planned
    if fallback:
        planned = [dict(m) for m in DEFAULT_MOVE[protocol]]
    sent = planned[:remaining]
    if protocol == "Sf":
        sent = sent[:1]
    return {
        "protocol": protocol,
        "extracted": extracted,
        "changes": changes,
        "fallback": fallback,
        "planned": planned,
        "sent": sent,
        "truncated_from": len(planned) if len(sent) < len(planned) else None,
        "remaining_before": remaining,
    }


# ═══════════════════════════════════════════════════════════
#  Legacy position map + knowledge (L382-482), verbatim logic
# ═══════════════════════════════════════════════════════════

def _yaw_to_dir(yaw):
    y = round(yaw) % 360
    if y < 45 or y >= 315: return "East"
    elif 45 <= y < 135: return "North"
    elif 135 <= y < 225: return "West"
    else: return "South"


class PositionTracker:
    def __init__(self, use_cardinal=True):
        self.positions = defaultdict(dict)       # cardinal labels
        self.positions_yaw = defaultdict(dict)   # raw yaw numbers
        self.visit_order = []
        self.use_cardinal = use_cardinal

    def update_from_log(self, log_entries):
        for e in log_entries:
            if not isinstance(e, dict):
                continue
            cmd = e.get('cmd', '')
            from_x = round(float(e.get('from_x', 0)))
            from_y = round(float(e.get('from_y', 0)))
            to_x = round(float(e.get('to_x', e.get('x', 0))))
            to_y = round(float(e.get('to_y', e.get('y', 0))))
            yaw = float(e.get('yaw', 0))
            blocked = e.get('blocked', False)
            from_key = (from_x, from_y)
            to_key = (to_x, to_y)
            if from_key not in self.visit_order:
                self.visit_order.append(from_key)
            if cmd in ('forward', 'backward') or cmd.startswith('forward(') or cmd.startswith('backward('):
                status = "BLOCKED" if blocked else "OPEN"
                direction = _yaw_to_dir(yaw)
                if cmd == 'backward' or cmd.startswith('backward('):
                    rev = {"East": "West", "West": "East", "North": "South", "South": "North"}
                    direction = rev[direction]
                self.positions[from_key][direction] = status
                yaw_key = f"yaw{round(yaw) % 360}"
                self.positions_yaw[from_key][yaw_key] = status
            if to_key not in self.visit_order:
                self.visit_order.append(to_key)

    def format_explored_map(self, cur_x, cur_y, cur_yaw):
        lines = ["EXPLORED POSITIONS:", ""]
        pos_data = self.positions if self.use_cardinal else self.positions_yaw
        for pos in self.visit_order:
            dirs = pos_data.get(pos, {})
            marker = " <-- YOU ARE HERE" if pos == (round(cur_x), round(cur_y)) else ""
            if dirs:
                dir_strs = [f"{d}={s}" for d, s in sorted(dirs.items())]
                lines.append(f"  ({pos[0]}, {pos[1]}): {', '.join(dir_strs)}{marker}")
            else:
                lines.append(f"  ({pos[0]}, {pos[1]}): (no forward attempts here){marker}")
        lines.append("")
        all_dirs = {}
        for pos, dirs in pos_data.items():
            for d, s in dirs.items():
                all_dirs[s] = all_dirs.get(s, 0) + 1
        lines.append(f"Total positions visited: {len(self.visit_order)}")
        lines.append(f"Walls found (BLOCKED): {all_dirs.get('BLOCKED', 0)}")
        lines.append(f"Open passages (OPEN): {all_dirs.get('OPEN', 0)}")
        if self.use_cardinal:
            lines.append(f"Current facing: {_yaw_to_dir(cur_yaw)} (yaw={cur_yaw:.0f})")
        else:
            lines.append(f"Current facing: yaw={cur_yaw:.0f}")
        return "\n".join(lines)


class KnowledgeManager:
    """Legacy accumulated thought/moves/results history; ``workspace=None`` keeps it in memory."""

    def __init__(self, workspace=None):
        self.path = (Path(workspace) / "knowledge.md") if workspace is not None else None
        self.entries = []

    def update(self, step, thought, moves, log_entries, screenshot_path=None):
        entry = f"## Step {step}\n\n"
        entry += f"**Thought**: {thought}\n\n"
        entry += f"**Moves**: {json.dumps(moves)}\n\n"
        if log_entries:
            entry += "**Results**:\n"
            for e in log_entries:
                status = "BLOCKED" if e.get("blocked") else "ok"
                entry += (f"- {e['cmd']}: pos=({e.get('to_x','?')}, {e.get('to_y','?')}) "
                          f"yaw={e.get('yaw','?')} {status}\n")
            entry += "\n"
        self.entries.append(entry)
        self._write()

    def _write(self):
        if self.path is None:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# Navigation Knowledge\n\n")
            for e in self.entries:
                f.write(e)

    def get_text(self):
        if not self.entries:
            return "(No observations yet.)"
        return "\n".join(self.entries)


def build_user_text(header, prev_log, cur_x, cur_y, raycast_text, tracker, cur_yaw,
                    knowledge, use_position_history=True):
    """Legacy per-turn user message (L838-868); only ``header`` differs from legacy."""
    text = f"{header}\n\n"

    # Previous move results
    if prev_log:
        text += "**Previous move results:**\n"
        for e in prev_log:
            if not isinstance(e, dict): continue
            status = "BLOCKED" if e.get("blocked") else "ok"
            text += (f"- {e['cmd']}: pos=({e.get('to_x','?')}, {e.get('to_y','?')}) "
                     f"yaw={e.get('yaw','?')} {status}\n")
        text += "\n"

    # Current position
    text += f"**Current position**: ({cur_x:.0f}, {cur_y:.0f})\n\n"

    # Raycast data (VRP)
    if raycast_text is not None:
        text += f"**Raycast scan:**\n{raycast_text}\n\n"

    # Position history (VP and VRP)
    if use_position_history:
        text += tracker.format_explored_map(cur_x, cur_y, cur_yaw) + "\n\n"

    # Knowledge
    knowledge_text = knowledge.get_text()
    if knowledge_text and knowledge_text != "(No observations yet.)":
        text += f"**Accumulated knowledge:**\n{knowledge_text}\n\n"

    text += USER_FOOTER
    return text


# ═══════════════════════════════════════════════════════════
#  Goal test (legacy centre; any logged entry of the call)
# ═══════════════════════════════════════════════════════════

def entry_xy(e):
    x, y = e.get("to_x", e.get("x")), e.get("to_y", e.get("y"))
    if _finite_number(x) and _finite_number(y):
        return float(x), float(y)
    return None


def goal_reached(entries, goal_x, goal_y, cell_size) -> bool:
    for e in entries:
        p = entry_xy(e) if isinstance(e, dict) else None
        if p is not None and math.hypot(p[0] - goal_x, p[1] - goal_y) <= 2 * cell_size:
            return True
    return False


# ═══════════════════════════════════════════════════════════
#  Legacy params generator (harness_win_tunnel/generate_params.py), no file I/O.
#  Used only for prompt-visible values: initial_yaw (first map "Current facing"),
#  start label (300, 300) and goal centre. Tunnel geometry does not depend on it.
# ═══════════════════════════════════════════════════════════

def legacy_params(seed=0, grid_size=5):
    rng = random.Random(seed)
    logical_rows = grid_size if grid_size else 5
    logical_cols = grid_size if grid_size else 5
    rows = 2 * logical_rows + 1
    cols = 2 * logical_cols + 1
    grid = [[1] * cols for _ in range(rows)]

    def cell_to_grid(r, c):
        return 2 * r + 1, 2 * c + 1

    visited = [[False] * logical_cols for _ in range(logical_rows)]
    stack = [(0, 0)]
    visited[0][0] = True
    gr, gc = cell_to_grid(0, 0)
    grid[gr][gc] = 0
    while stack:
        cr, cc = stack[-1]
        neighbors = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < logical_rows and 0 <= nc < logical_cols and not visited[nr][nc]:
                neighbors.append((nr, nc, dr, dc))
        if not neighbors:
            stack.pop()
            continue
        nr, nc, dr, dc = rng.choice(neighbors)
        visited[nr][nc] = True
        wr, wc = cell_to_grid(cr, cc)
        grid[wr + dr][wc + dc] = 0
        gr2, gc2 = cell_to_grid(nr, nc)
        grid[gr2][gc2] = 0
        stack.append((nr, nc))

    interior_walls = []
    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            if grid[r][c] == 1:
                if r % 2 == 0 and c % 2 == 1:
                    if grid[r-1][c] == 0 and grid[r+1][c] == 0:
                        interior_walls.append((r, c))
                if r % 2 == 1 and c % 2 == 0:
                    if grid[r][c-1] == 0 and grid[r][c+1] == 0:
                        interior_walls.append((r, c))
    n_loops = max(1, int(len(interior_walls) * 0.2))
    rng.shuffle(interior_walls)
    loops_added = 0
    for r, c in interior_walls[:n_loops]:
        grid[r][c] = 0
        loops_added += 1

    dead_end_candidates = []
    for r in range(2, rows - 2):
        for c in range(2, cols - 2):
            if grid[r][c] == 1:
                open_adj = sum(1 for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]
                               if grid[r+dr][c+dc] == 0)
                if open_adj == 1:
                    dead_end_candidates.append((r, c))
    n_deadends = min(4, len(dead_end_candidates))
    rng.shuffle(dead_end_candidates)
    deadends_added = 0
    for r, c in dead_end_candidates[:n_deadends]:
        grid[r][c] = 0
        deadends_added += 1

    start_grid = [1, 1]
    goal_grid = [2 * logical_rows - 1, 2 * logical_cols - 1]

    def bfs(grid, start, goal):
        q = deque([(start, [start])])
        seen = {tuple(start)}
        while q:
            (r, c), path = q.popleft()
            if [r, c] == goal:
                return path
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]) and grid[nr][nc] == 0 and (nr, nc) not in seen:
                    seen.add((nr, nc))
                    q.append(([nr, nc], path + [[nr, nc]]))
        return None

    solution_path = bfs(grid, start_grid, goal_grid)
    assert solution_path is not None, "No solution found"

    dead_end_count = 0
    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            if grid[r][c] == 0 and r % 2 == 1 and c % 2 == 1:
                open_neighbors = sum(1 for dr, dc in [(-1,0),(1,0),(0,-1),(0,1)]
                                     if grid[r+dr][c+dc] == 0)
                if open_neighbors == 1:
                    dead_end_count += 1

    cell_size = 200.0
    wall_height = 400.0

    def grid_to_world(r, c):
        return (c + 0.5) * cell_size, (r + 0.5) * cell_size

    solution_world = [list(grid_to_world(r, c)) for r, c in solution_path]
    if len(solution_path) >= 2:
        sx, sy = grid_to_world(*start_grid)
        nx, ny = grid_to_world(*solution_path[1])
        initial_yaw = math.degrees(math.atan2(ny - sy, nx - sx))
    else:
        initial_yaw = 0.0

    return {
        "seed": seed,
        "maze_type": "complex_with_deadends",
        "logical_rows": logical_rows,
        "logical_cols": logical_cols,
        "grid_rows": rows,
        "grid_cols": cols,
        "grid": grid,
        "start_grid": start_grid,
        "goal_grid": goal_grid,
        "solution_path": solution_path,
        "solution_world": solution_world,
        "initial_yaw": initial_yaw,
        "cell_size": cell_size,
        "wall_height": wall_height,
        "loops_added": loops_added,
        "deadends_added": deadends_added,
        "dead_end_count": dead_end_count,
    }


# ═══════════════════════════════════════════════════════════
#  Episode
# ═══════════════════════════════════════════════════════════

@dataclass
class EpisodeResult:
    run_id: str
    model: str
    result: str
    steps: int
    moves_used: int
    first_goal_move: Optional[int]
    move_budget: int
    final_distance: float
    artifacts: dict = field(default_factory=dict)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


async def run(engine, seed, vlm, logger, *, protocol, info, tunnel_radius, colorful, cardinal,
              move_budget, max_calls, repeat=0) -> EpisodeResult:
    if protocol not in PROTOCOLS:
        raise ValueError(f"protocol must be one of {PROTOCOLS}")
    if info not in INFOS:
        raise ValueError(f"info must be one of {INFOS}")
    move_budget, max_calls = int(move_budget), int(max_calls)
    if move_budget < 1 or max_calls < 1:
        raise ValueError("move_budget and max_calls must be >= 1")

    # Release helpers (absolute imports; engine/package needed only from here on).
    from veriworld.common import format_prompt_txt, png_to_base64_url
    from veriworld.common.screenshot import default_screenshot_dir
    from ...generate_params import (
        generate as generate_release_params,
    )
    from .._common import (
        CLEAN_VOXEL_CODE,
        LEVEL_SWITCH_CODE,
        RAYCAST_CODE_3D,
        build_setup_code,
        parse_navlog,
        parse_raycast,
        take_screenshot,
        unwrap,
        wait_scene_ready,
    )

    move_camera_code = MOVE_CAMERA_FIXED_PATH.read_text(encoding="utf-8")
    move_camera_sha = hashlib.sha256(move_camera_code.encode("utf-8")).hexdigest()

    # Engine-side params: release generator (what ue_setup / wait_scene_ready consume;
    # the tunnel itself comes from ue_setup's fixed Random(99)).
    params = generate_release_params(seed=seed)
    # Prompt-side values: legacy generator, as the paper's harness used them.
    lparams = legacy_params(seed=seed, grid_size=params["logical_rows"])
    cell_size = lparams["cell_size"]
    start_grid = lparams["start_grid"]
    goal_grid = lparams["goal_grid"]
    yaw_val = float(lparams["initial_yaw"])
    use = INFO_MAP[info]

    card_tag = "card" if cardinal else "nocard"
    run_id = (f"{_safe(vlm.model)}_{info}_{protocol}eq_hole{int(tunnel_radius)}_"
              f"{'color' if colorful else 'mono'}_m{move_budget}_c{max_calls}_{card_tag}_seed{seed}_r{repeat}")
    workspace = logger.model_dir(run_id)
    cfg = {
        "run_id": run_id, "model": vlm.model, "seed": seed, "repeat": repeat,
        "harness": "tunnel_legacy_eq", "protocol": protocol, "info": info,
        "tunnel_radius": tunnel_radius, "colorful": colorful, "cardinal": cardinal,
        "move_budget": move_budget, "max_calls": max_calls,
        "initial_yaw_prompt": yaw_val, "initial_yaw_release_params": params.get("initial_yaw"),
        "goal_centre": [(goal_grid[1] + 0.5) * cell_size, (goal_grid[0] + 0.5) * cell_size],
        "goal_threshold_cm": cell_size * 2,
        "move_camera_fixed_sha256": move_camera_sha,
        **use,
    }
    (workspace / "params.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
    (workspace / "legacy_params.json").write_text(json.dumps(lparams, indent=2), encoding="utf-8")
    logger.snapshot_model(
        task_module=__name__,
        model_name=run_id,
        seed=seed,
        resolved_args={
            "seed": seed, "model": vlm.model, "protocol": protocol, "info": info,
            "tunnel_radius": tunnel_radius, "colorful": colorful, "cardinal": cardinal,
            "move_budget": move_budget, "max_calls": max_calls, "repeat": repeat,
        },
        extra_config=cfg,
    )

    # ── Engine setup (release scaffold) ──
    if "LEVEL_SWITCHING" in unwrap(await engine.python_exec(LEVEL_SWITCH_CODE)):
        await asyncio.sleep(5)
    unwrap(await engine.python_exec(CLEAN_VOXEL_CODE))
    unwrap(await engine.python_exec(build_setup_code(params, tunnel_radius, colorful)))
    await asyncio.sleep(2)
    unwrap(await engine.python_exec(move_camera_code))  # fixed logging, not _common.MOVE_CAMERA_CODE
    await asyncio.sleep(0.5)
    if "NAV_RESET" not in unwrap(await engine.python_exec(
            "import builtins; builtins._NAV_MOVES = None; builtins._NAV_LOG_LIST = []; print('NAV_RESET')")):
        raise RuntimeError("Navigation controller reset receipt missing")

    await wait_scene_ready(engine, params, workspace)

    screenshot_dir = default_screenshot_dir("demo1")
    init_shot = await take_screenshot(engine, "step_000", screenshot_dir, workspace)

    system_prompt = build_system_prompt(protocol, info, move_budget)
    tracker = PositionTracker(use_cardinal=cardinal)
    knowledge = KnowledgeManager(workspace)

    cur_x = (start_grid[1] + 0.5) * cell_size
    cur_y = (start_grid[0] + 0.5) * cell_size
    cur_yaw = yaw_val
    goal_x = (goal_grid[1] + 0.5) * cell_size
    goal_y = (goal_grid[0] + 0.5) * cell_size
    tracker.visit_order.append((round(cur_x), round(cur_y)))

    prev_log: list = []
    calls = 0
    used = 0
    first_goal_move = None
    passed = False

    for step in range(1, max_calls + 1):
        if used >= move_budget:
            break
        shot = init_shot if step == 1 else await take_screenshot(
            engine, f"step_{step - 1:03d}", screenshot_dir, workspace)
        parts: list = [{"type": "image_url", "image_url": {"url": png_to_base64_url(shot)}}]

        step_raycast = None
        if use["raycast"]:
            step_raycast = parse_raycast(unwrap(await engine.python_exec(RAYCAST_CODE_3D)))
            (workspace / f"step_{step:03d}_raycast.txt").write_text(step_raycast, encoding="utf-8")

        text = build_user_text(budget_header(step, used, move_budget), prev_log, cur_x, cur_y,
                               step_raycast, tracker, cur_yaw, knowledge,
                               use_position_history=use["position_history"])
        parts.append({"type": "text", "text": text})
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ]
        (workspace / f"step_{step:03d}_prompt.txt").write_text(
            format_prompt_txt(messages, [shot.name]), encoding="utf-8")

        t0 = time.time()
        try:
            response = await asyncio.get_event_loop().run_in_executor(None, vlm.chat, messages)
        except Exception as e:  # noqa: BLE001
            _write_json(workspace / f"step_{step:03d}_transport_error.json",
                        {"kind": "api_error", "error_type": type(e).__name__, "scored": False})
            raise RuntimeError("Navigation API request failed; episode is unscored") from e
        except BaseException as e:  # transport abort / cancellation: record, then propagate
            _write_json(workspace / f"step_{step:03d}_transport_error.json",
                        {"kind": "api_abort", "error_type": type(e).__name__, "scored": False})
            raise
        calls += 1
        log.info("step %d: VLM %.1fs", step, time.time() - t0)

        (workspace / f"step_{step:03d}_response.txt").write_text(response, encoding="utf-8")
        raw_response = getattr(vlm, "last_response", None)
        if raw_response is not None:
            _write_json(workspace / f"step_{step:03d}_response.json", raw_response)
        thought = extract_thought(response)
        (workspace / f"step_{step:03d}_thought.txt").write_text(thought, encoding="utf-8")

        plan = plan_moves(protocol, response, move_budget - used)
        sent = plan["sent"]
        if plan["changes"]:
            _write_json(workspace / f"step_{step:03d}_sanitized.json", plan["changes"])
        _write_json(workspace / f"step_{step:03d}_plan.json", plan)
        _write_json(workspace / f"step_{step:03d}_sent.json", sent)

        # json.loads on the engine side: model items may contain JSON-only literals
        # (true/false/null) that are not valid Python source.
        receipt = unwrap(await engine.python_exec(
            "import builtins, json; builtins._NAV_MOVES = json.loads("
            + json.dumps(json.dumps(sent)) + "); builtins._NAV_DONE = False; builtins._NAV_LOG = []; "
            "print('NAV_SENT')"))
        if "NAV_SENT" not in receipt:
            _write_json(workspace / f"step_{step:03d}_transport_error.json",
                        {"kind": "command_send_failed", "scored": False})
            raise RuntimeError("Navigation command send receipt missing")
        used += len(sent)

        polls = max(60, 20 * len(sent))  # release: 60 x 0.5 s; +10 s per command beyond 3
        for _ in range(polls):
            await asyncio.sleep(0.5)
            if "True" in unwrap(await engine.python_exec(
                    "import builtins; print(getattr(builtins, '_NAV_DONE', False))")):
                break
        else:
            _write_json(workspace / f"step_{step:03d}_transport_error.json",
                        {"kind": "movement_timeout", "scored": False, "commands": len(sent)})
            raise TimeoutError("Navigation controller did not finish the committed action")

        await asyncio.sleep(0.3)
        log_out = unwrap(await engine.python_exec(
            "import builtins, json\n"
            "log = getattr(builtins, '_NAV_LOG', [])\n"
            "if isinstance(log, dict): log = [log]\n"
            "print('NAVLOG:' + json.dumps(log))"
        ))
        log_entries = parse_navlog(log_out)
        (workspace / f"step_{step:03d}_movelog.txt").write_text(
            json.dumps(log_entries, indent=2), encoding="utf-8")
        if len(log_entries) != len(sent):
            _write_json(workspace / f"step_{step:03d}_transport_error.json",
                        {"kind": "navlog_count_mismatch", "scored": False,
                         "sent": len(sent), "logged": len(log_entries)})
            raise RuntimeError("Navigation log does not match the commands sent")

        tracker.update_from_log(log_entries)
        last = log_entries[-1]
        cur_x = float(last.get("to_x", last.get("x", cur_x)))
        cur_y = float(last.get("to_y", last.get("y", cur_y)))
        cur_yaw = float(last.get("yaw", cur_yaw))

        knowledge.update(step, thought, sent, log_entries)
        prev_log = log_entries

        if goal_reached(log_entries, goal_x, goal_y, cell_size):
            first_goal_move = used
            passed = True
            break

    final_dist = math.sqrt((cur_x - goal_x) ** 2 + (cur_y - goal_y) ** 2)
    result = "PASS" if passed else "FAIL"
    summary = {
        "run_id": run_id, "model": vlm.model, "seed": seed,
        "protocol": protocol, "info": info,
        "tunnel_radius": tunnel_radius, "colorful": colorful, "cardinal": cardinal,
        "repeat": repeat, "result": result,
        "steps": calls, "moves_used": used, "first_goal_move": first_goal_move,
        "move_budget": move_budget, "max_calls": max_calls,
        "final_distance": round(final_dist, 1),
        "positions_explored": len(tracker.visit_order),
        "goal_centre": [goal_x, goal_y],
        "stop_reason": "goal" if passed else ("budget" if used >= move_budget else "max_calls"),
        "harness": "tunnel_legacy_eq",
    }
    (workspace / "episode_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info("%s: %s in %d calls / %d moves (dist=%.0f)", run_id, result, calls, used, final_dist)
    return EpisodeResult(
        run_id=run_id, model=vlm.model, result=result, steps=calls, moves_used=used,
        first_goal_move=first_goal_move, move_budget=move_budget,
        final_distance=round(final_dist, 1), artifacts={"workspace": str(workspace)},
    )


__all__ = [
    "run", "EpisodeResult", "build_system_prompt", "legacy_system_prompt", "budget_header",
    "build_user_text", "plan_moves", "sanitize", "extract_moves_batch", "extract_moves_single_free",
    "extract_moves_aim_and_fly", "extract_thought", "PositionTracker", "KnowledgeManager",
    "goal_reached", "legacy_params", "BUDGET_SENTENCE",
]
