"""Harness-local helpers for mazenavfps.harness_knowledge.

Ported from the legacy ``13b_maze_nav_knowledge/agent_harness_visual.py``
with minimal semantic change. The harness treats the task as a
per-round computational problem:

1. Round 0 flies a bird's-eye camera over the maze (no ball) and
   records an observation video.
2. Rounds 1..N ask the agent for a Python pathfinding snippet, wrap
   it in a scene template, run it in UE, record the ball animation,
   and verify the resulting waypoint sequence with a Bresenham
   wall-crossing check.
3. Between rounds the harness calls an **extra** LLM turn to rewrite
   ``knowledge.md`` — a natural-language memory document the agent
   reads first in the next round.

Kept in one file to keep the harness self-contained; future ablations
(``pv_kn``, ``vp_kn``, …) under this harness import from here.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

HERE = Path(__file__).parent
# Task-level (shared across harnesses) — ``generate_params.py`` lives
# at the parent of this harness folder.
TASK_ROOT = HERE.parent


# ---------------------------------------------------------------------------
# UE-side scene code (strings — substituted + sent via engine.python_exec)
# ---------------------------------------------------------------------------

# Bird's-eye flyover for Round 0 observation. Loads params from
# ``{{BASE}}/params.json`` — harness substitutes BASE with the
# per-worker workspace path before exec.
SETUP_OBSERVE_CODE = r'''import unreal_runtime as ur
import json
import os

BASE = r"{{BASE}}"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

grid = params["grid"]
grid_rows = params["grid_rows"]
grid_cols = params["grid_cols"]
cell_size = params["cell_size"]
wall_height = params["wall_height"]
start_grid = params["start_grid"]
goal_grid = params["goal_grid"]

world = ur.Engine.GetDefaultWorld()
lib = ur.Engine.RuntimeMaterialLibrary

# Clean any leftover maze actors from a previous run.
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("MZ_"):
        a.K2_DestroyActor()

cube_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cube.Cube")
sphere_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, 0, 0))

props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Opaque", False)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.55, 0.55, 0.6);\n"
    "PixelMaterialInputs.Roughness = 0.8;",
    "MZ_WallMat", props, True)
wall_mat = lib.GetRuntimeMaterial("MZ_WallMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.85, 0.85, 0.8);\n"
    "PixelMaterialInputs.Roughness = 0.9;",
    "MZ_FloorMat", props, True)
floor_mat = lib.GetRuntimeMaterial("MZ_FloorMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.1, 0.8, 0.2);\n"
    "PixelMaterialInputs.Roughness = 0.3;",
    "MZ_StartMat", props, True)
start_mat = lib.GetRuntimeMaterial("MZ_StartMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.9, 0.15, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.3;",
    "MZ_GoalMat", props, True)
goal_mat = lib.GetRuntimeMaterial("MZ_GoalMat")


def _spawn_cube(pos, scale, label, mat):
    t = ur.CoreUObject.Transform()
    t.Translation = ur.CoreUObject.Vector(pos[0], pos[1], pos[2])
    t.Rotation = quat
    sv = ur.CoreUObject.Vector(); sv.X, sv.Y, sv.Z = scale
    t.Scale3D = sv
    actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    mc = actor.StaticMeshComponent
    mc.SetMobility(2)
    mc.SetStaticMesh(cube_mesh)
    mc.SetCollisionEnabled(0)
    if mat:
        mc.SetMaterial(0, mat)
    actor.SetActorLabel(label)
    return actor


def _grid_to_world(r, c):
    return (c + 0.5) * cell_size, (r + 0.5) * cell_size


wall_sx = cell_size / 100.0
wall_sy = cell_size / 100.0
wall_sz = wall_height / 100.0
for r in range(grid_rows):
    for c in range(grid_cols):
        if grid[r][c] == 1:
            wx, wy = _grid_to_world(r, c)
            _spawn_cube([wx, wy, wall_height / 2.0],
                        [wall_sx, wall_sy, wall_sz],
                        f"MZ_W_{r}_{c}", wall_mat)

floor_cx = grid_cols * cell_size / 2.0
floor_cy = grid_rows * cell_size / 2.0
_spawn_cube([floor_cx, floor_cy, -5.0],
            [grid_cols * cell_size / 100.0, grid_rows * cell_size / 100.0, 0.1],
            "MZ_Floor", floor_mat)

sx, sy = _grid_to_world(*start_grid)
gx, gy = _grid_to_world(*goal_grid)

st = ur.CoreUObject.Transform()
st.Translation = ur.CoreUObject.Vector(sx, sy, 30.0)
st.Rotation = quat
ssv = ur.CoreUObject.Vector(); ssv.X = ssv.Y = ssv.Z = 1.5
st.Scale3D = ssv
sa = world.SpawnActorEx(ur.Engine.StaticMeshActor, st, 1)
sa.StaticMeshComponent.SetMobility(2)
sa.StaticMeshComponent.SetStaticMesh(sphere_mesh)
sa.StaticMeshComponent.SetCollisionEnabled(0)
if start_mat:
    sa.StaticMeshComponent.SetMaterial(0, start_mat)
sa.SetActorLabel("MZ_Start")

gt = ur.CoreUObject.Transform()
gt.Translation = ur.CoreUObject.Vector(gx, gy, 30.0)
gt.Rotation = quat
gsv = ur.CoreUObject.Vector(); gsv.X = gsv.Y = gsv.Z = 1.5
gt.Scale3D = gsv
ga = world.SpawnActorEx(ur.Engine.StaticMeshActor, gt, 1)
ga.StaticMeshComponent.SetMobility(2)
ga.StaticMeshComponent.SetStaticMesh(sphere_mesh)
ga.StaticMeshComponent.SetCollisionEnabled(0)
if goal_mat:
    ga.StaticMeshComponent.SetMaterial(0, goal_mat)
ga.SetActorLabel("MZ_Goal")

cam_z = max(grid_rows, grid_cols) * cell_size * 1.2 + wall_height
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]
    controller = pawn.GetController()
    pawn.K2_SetActorLocation(
        ur.CoreUObject.Vector(floor_cx, floor_cy, cam_z), False)
    cam_rot = ur.CoreUObject.Rotator()
    cam_rot.Pitch = -90.0; cam_rot.Yaw = 0.0; cam_rot.Roll = 0.0
    controller.SetControlRotation(cam_rot)

print(f"[observe] maze {grid_rows}x{grid_cols}, bird's-eye camera at z={cam_z:.0f}")
'''


# Round 1..N scene: wraps the agent's BFS code and animates a ball
# along the produced waypoints. Substitution: {{BASE}} + {{AGENT_CODE}}.
# The agent-code block defines a ``path`` variable; SCENE_SUFFIX handles
# the rest (fallback, animation, wall marking, result log).
SCENE_PREFIX = r'''import unreal_runtime as ur
import json
import os
import math
from collections import deque

BASE = r"{{BASE}}"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

grid = params["grid"]
grid_rows = params["grid_rows"]
grid_cols = params["grid_cols"]
cell_size = params["cell_size"]
wall_height = params["wall_height"]
start_grid = params["start_grid"]
goal_grid = params["goal_grid"]

LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")
WAYPOINTS_PATH = os.path.join(BASE, "waypoints.json")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# ====== AGENT CODE START ======
'''

SCENE_SUFFIX = r'''
# ====== AGENT CODE END ======

# Fallback: if BFS produced <2 cells, substitute a straight-line path
# so the ball still moves and the video shows wall violations.
if not isinstance(path, (list, tuple)) or len(path) < 2:
    print(f"WARNING: path has only {len(path) if hasattr(path, '__len__') else '?'} cell(s). "
          "Falling back to straight line.")
    sr, sc = start_grid
    gr, gc = goal_grid
    fallback = [(sr, sc)]
    r, c = sr, sc
    while (r, c) != (gr, gc):
        if r < gr: r += 1
        elif r > gr: r -= 1
        elif c < gc: c += 1
        elif c > gc: c -= 1
        fallback.append((r, c))
    path = fallback


def _grid_to_world(r, c):
    return (c + 0.5) * cell_size, (r + 0.5) * cell_size


path_world = [list(_grid_to_world(r, c)) for (r, c) in path]

with open(WAYPOINTS_PATH, "w") as f:
    json.dump({"waypoints": path_world}, f)
print(f"Saved {len(path_world)} waypoints")

world = ur.Engine.GetDefaultWorld()
lib = ur.Engine.RuntimeMaterialLibrary
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("MZ_"):
        a.K2_DestroyActor()

cube_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cube.Cube")
sphere_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, 0, 0))

props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Opaque", False)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.55, 0.55, 0.6);\n"
    "PixelMaterialInputs.Roughness = 0.8;",
    "MZ_WallMat", props, True)
wall_mat = lib.GetRuntimeMaterial("MZ_WallMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.85, 0.85, 0.8);\n"
    "PixelMaterialInputs.Roughness = 0.9;",
    "MZ_FloorMat", props, True)
floor_mat = lib.GetRuntimeMaterial("MZ_FloorMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.1, 0.8, 0.2);\n"
    "PixelMaterialInputs.Roughness = 0.3;",
    "MZ_StartMat", props, True)
start_mat = lib.GetRuntimeMaterial("MZ_StartMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.9, 0.15, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.3;",
    "MZ_GoalMat", props, True)
goal_mat = lib.GetRuntimeMaterial("MZ_GoalMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(1.0, 0.9, 0.0);\n"
    "PixelMaterialInputs.Roughness = 0.2;",
    "MZ_BallMat", props, True)
ball_mat = lib.GetRuntimeMaterial("MZ_BallMat")
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(8.0, 0.2, 0.2);\n"
    "PixelMaterialInputs.BaseColor = float3(1.0, 0.05, 0.05);\n"
    "PixelMaterialInputs.Roughness = 0.3;",
    "MZ_ViolationMat", props, True)
violation_mat = lib.GetRuntimeMaterial("MZ_ViolationMat")


def _spawn_cube(pos, scale, label, mat):
    t = ur.CoreUObject.Transform()
    t.Translation = ur.CoreUObject.Vector(pos[0], pos[1], pos[2])
    t.Rotation = quat
    sv = ur.CoreUObject.Vector(); sv.X, sv.Y, sv.Z = scale
    t.Scale3D = sv
    actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    mc = actor.StaticMeshComponent
    mc.SetMobility(2)
    mc.SetStaticMesh(cube_mesh)
    mc.SetCollisionEnabled(0)
    if mat:
        mc.SetMaterial(0, mat)
    actor.SetActorLabel(label)
    return actor


wall_sx = cell_size / 100.0
wall_sy = cell_size / 100.0
wall_sz = wall_height / 100.0
for r in range(grid_rows):
    for c in range(grid_cols):
        if grid[r][c] == 1:
            wx, wy = _grid_to_world(r, c)
            _spawn_cube([wx, wy, wall_height / 2.0],
                        [wall_sx, wall_sy, wall_sz],
                        f"MZ_W_{r}_{c}", wall_mat)

floor_cx = grid_cols * cell_size / 2.0
floor_cy = grid_rows * cell_size / 2.0
_spawn_cube([floor_cx, floor_cy, -5.0],
            [grid_cols * cell_size / 100.0, grid_rows * cell_size / 100.0, 0.1],
            "MZ_Floor", floor_mat)

sx, sy = _grid_to_world(*start_grid)
gx, gy = _grid_to_world(*goal_grid)

st = ur.CoreUObject.Transform()
st.Translation = ur.CoreUObject.Vector(sx, sy, 30.0)
st.Rotation = quat
ssv = ur.CoreUObject.Vector(); ssv.X = ssv.Y = ssv.Z = 1.5
st.Scale3D = ssv
sa = world.SpawnActorEx(ur.Engine.StaticMeshActor, st, 1)
sa.StaticMeshComponent.SetMobility(2)
sa.StaticMeshComponent.SetStaticMesh(sphere_mesh)
sa.StaticMeshComponent.SetCollisionEnabled(0)
if start_mat:
    sa.StaticMeshComponent.SetMaterial(0, start_mat)
sa.SetActorLabel("MZ_Start")

gt = ur.CoreUObject.Transform()
gt.Translation = ur.CoreUObject.Vector(gx, gy, 30.0)
gt.Rotation = quat
gsv = ur.CoreUObject.Vector(); gsv.X = gsv.Y = gsv.Z = 1.5
gt.Scale3D = gsv
ga = world.SpawnActorEx(ur.Engine.StaticMeshActor, gt, 1)
ga.StaticMeshComponent.SetMobility(2)
ga.StaticMeshComponent.SetStaticMesh(sphere_mesh)
ga.StaticMeshComponent.SetCollisionEnabled(0)
if goal_mat:
    ga.StaticMeshComponent.SetMaterial(0, goal_mat)
ga.SetActorLabel("MZ_Goal")

nav_t = ur.CoreUObject.Transform()
nav_t.Translation = ur.CoreUObject.Vector(path_world[0][0], path_world[0][1], 40.0)
nav_t.Rotation = quat
nav_sv = ur.CoreUObject.Vector(); nav_sv.X = nav_sv.Y = nav_sv.Z = 1.0
nav_t.Scale3D = nav_sv
nav_ball = world.SpawnActorEx(ur.Engine.StaticMeshActor, nav_t, 1)
nav_ball.StaticMeshComponent.SetMobility(2)
nav_ball.StaticMeshComponent.SetStaticMesh(sphere_mesh)
nav_ball.StaticMeshComponent.SetCollisionEnabled(0)
if ball_mat:
    nav_ball.StaticMeshComponent.SetMaterial(0, ball_mat)
nav_ball.SetActorLabel("MZ_NavBall")

cam_z = max(grid_rows, grid_cols) * cell_size * 1.2 + wall_height
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]
    controller = pawn.GetController()
    pawn.K2_SetActorLocation(
        ur.CoreUObject.Vector(floor_cx, floor_cy, cam_z), False)
    cam_rot = ur.CoreUObject.Rotator()
    cam_rot.Pitch = -90.0; cam_rot.Yaw = 0.0; cam_rot.Roll = 0.0
    controller.SetControlRotation(cam_rot)

NAV_SPEED = 800.0


def _is_wall(gr, gc):
    if gr < 0 or gr >= grid_rows or gc < 0 or gc >= grid_cols:
        return True
    return grid[gr][gc] == 1


log_file = open(LOG_PATH, "w", encoding="utf-8")
log_file.write("frame,elapsed,bx,by,grid_r,grid_c,is_wall,waypoint_idx,status\n")


def _navigate_tick(delta_time, elapsed_time, actors, ctx):
    ball = ctx["ball"]
    wp = ctx["waypoints"]
    idx = ctx["idx"]
    progress = ctx["progress"]
    log = ctx["log"]
    ctx["frame"] = ctx.get("frame", 0) + 1
    frame = ctx["frame"]

    if idx >= len(wp) - 1:
        fx, fy = wp[-1]
        ball.K2_SetActorLocation(ur.CoreUObject.Vector(fx, fy, 40.0), False)
        if not ctx.get("done"):
            dx = fx - gx; dy = fy - gy
            dist = math.sqrt(dx * dx + dy * dy)
            hit = dist < cell_size
            status = "PASS" if hit else "FAIL"
            gr_f = int(fy / cell_size)
            gc_f = int(fx / cell_size)
            log.write(f"{frame},{elapsed_time:.3f},{fx:.1f},{fy:.1f},"
                      f"{gr_f},{gc_f},{_is_wall(gr_f, gc_f)},{idx},NAVIGATION_{status}\n")
            log.write(f"\n# RESULT: {status}\n")
            log.flush(); log.close()
            ctx["done"] = True
        return True

    cx, cy = wp[idx]
    nx, ny = wp[idx + 1]
    dx = nx - cx; dy = ny - cy
    seg_len = math.sqrt(dx * dx + dy * dy)
    if seg_len < 0.01:
        seg_len = 0.01

    progress += NAV_SPEED * delta_time
    while progress >= seg_len:
        progress -= seg_len
        idx += 1
        if idx >= len(wp) - 1:
            break
        cx, cy = wp[idx]
        nx, ny = wp[idx + 1]
        dx = nx - cx; dy = ny - cy
        seg_len = math.sqrt(dx * dx + dy * dy)
        if seg_len < 0.01:
            seg_len = 0.01

    ctx["idx"] = idx
    ctx["progress"] = progress
    if idx >= len(wp) - 1:
        return True

    t = min(progress / seg_len, 1.0)
    px = cx + dx * t
    py = cy + dy * t

    gr_p = int(py / cell_size)
    gc_p = int(px / cell_size)
    if _is_wall(gr_p, gc_p):
        ctx["violations"] = ctx.get("violations", 0) + 1
        key = (gr_p, gc_p)
        if key not in ctx.get("marked_walls", set()):
            ctx.setdefault("marked_walls", set()).add(key)
            label = f"MZ_W_{gr_p}_{gc_p}"
            for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
                if a.GetActorLabel() == label:
                    a.StaticMeshComponent.SetMaterial(0, violation_mat)
                    break

    ball.K2_SetActorLocation(ur.CoreUObject.Vector(px, py, 40.0), False)

    if frame % 10 == 0:
        log.write(f"{frame},{elapsed_time:.3f},{px:.1f},{py:.1f},"
                  f"{gr_p},{gc_p},{_is_wall(gr_p, gc_p)},{idx},navigating\n")
    return True


ur.submit_tick_task("maze_nav", _navigate_tick, [],
                    {"ball": nav_ball, "waypoints": path_world,
                     "idx": 0, "progress": 0.0, "done": False,
                     "frame": 0, "log": log_file},
                    max_duration=60.0)
print(f"Scene ready. Animating {len(path_world)} waypoints.")
'''


def assemble_scene(agent_code: str, workspace: Path) -> str:
    """Wrap the agent's pathfinding code in the harness scene template.

    The agent's snippet must set a ``path`` variable (list of
    ``(row, col)`` tuples). Everything else — scene setup, ball
    animation, wall-violation highlighting, result log — is owned by
    this template.
    """
    base = str(workspace).replace("\\", "/")
    prefix = SCENE_PREFIX.replace("{{BASE}}", base)
    suffix = SCENE_SUFFIX
    return prefix + agent_code + "\n" + suffix


def patch_observe_code(workspace: Path) -> str:
    return SETUP_OBSERVE_CODE.replace("{{BASE}}", str(workspace).replace("\\", "/"))


# ---------------------------------------------------------------------------
# Post-hoc verification — strict wall-crossing check
# ---------------------------------------------------------------------------
def _world_to_grid(wx: float, wy: float, cell_size: float) -> Tuple[int, int]:
    return int(wy / cell_size), int(wx / cell_size)


def _bresenham(r1: int, c1: int, r2: int, c2: int) -> List[Tuple[int, int]]:
    cells: List[Tuple[int, int]] = []
    dr = abs(r2 - r1); dc = abs(c2 - c1)
    sr = 1 if r1 < r2 else (-1 if r1 > r2 else 0)
    sc = 1 if c1 < c2 else (-1 if c1 > c2 else 0)
    err = dr - dc
    r, c = r1, c1
    for _ in range(dr + dc + 1):
        cells.append((r, c))
        if r == r2 and c == c2:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc; r += sr
        if e2 < dr:
            err += dr; c += sc
    return cells


def verify_waypoints(waypoints_world: List[List[float]], params: dict) -> Tuple[Optional[str], str]:
    """Strict wall-crossing verification — pure Python, no UE.

    Returns ``(result, log_text)`` where ``result`` is ``"PASS"`` or
    ``"FAIL"`` (never None for validly-structured input) and
    ``log_text`` is a human-readable cell-by-cell trace. The log
    format matches legacy ``verify.py`` so ``extract_facts_from_verify``
    can parse wall/open claims out of it verbatim.
    """
    lines: List[str] = []
    grid = params["grid"]
    grid_rows = params["grid_rows"]
    grid_cols = params["grid_cols"]
    start_grid = tuple(params["start_grid"])
    goal_grid = tuple(params["goal_grid"])
    cell_size = params["cell_size"]

    lines.append(f"Maze: {grid_rows}x{grid_cols}, cell_size={cell_size}cm")
    lines.append(f"Start: grid{start_grid}, Goal: grid{goal_grid}")

    if len(waypoints_world) < 2:
        lines.append(f"ERROR: need >=2 waypoints, got {len(waypoints_world)}")
        lines.append("RESULT: FAIL")
        return "FAIL", "\n".join(lines)

    grid_cells: List[Tuple[int, int]] = []
    for wp in waypoints_world:
        wx, wy = wp[0], wp[1]
        grid_cells.append(_world_to_grid(wx, wy, cell_size))

    deduped: List[Tuple[int, int]] = [grid_cells[0]]
    for gc in grid_cells[1:]:
        if gc != deduped[-1]:
            deduped.append(gc)

    lines.append(f"Agent waypoints: {len(waypoints_world)}")
    lines.append(f"Grid cells (deduped): {len(deduped)}")
    for i, (r, c) in enumerate(deduped):
        in_bounds = 0 <= r < grid_rows and 0 <= c < grid_cols
        status = "open" if (in_bounds and grid[r][c] == 0) else "WALL"
        lines.append(f"  [{i}] grid({r},{c}) {status}")

    if deduped[0] != start_grid:
        lines.append(f"FAIL: path starts at {deduped[0]}, expected {start_grid}")
        lines.append("RESULT: FAIL")
        return "FAIL", "\n".join(lines)

    if deduped[-1] != goal_grid:
        lines.append(f"FAIL: path ends at {deduped[-1]}, expected {goal_grid}")
        lines.append("RESULT: FAIL")
        return "FAIL", "\n".join(lines)

    def _open(r: int, c: int) -> bool:
        return 0 <= r < grid_rows and 0 <= c < grid_cols and grid[r][c] == 0

    for i, (r, c) in enumerate(deduped):
        if not _open(r, c):
            lines.append(f"FAIL: cell [{i}] grid({r},{c}) is a wall")
            lines.append("RESULT: FAIL")
            return "FAIL", "\n".join(lines)

    for i in range(len(deduped) - 1):
        r1, c1 = deduped[i]
        r2, c2 = deduped[i + 1]
        for r, c in _bresenham(r1, c1, r2, c2):
            if not _open(r, c):
                lines.append(f"FAIL: wall at grid({r},{c}) between "
                             f"cells [{i}]({r1},{c1}) and [{i+1}]({r2},{c2})")
                lines.append("RESULT: FAIL")
                return "FAIL", "\n".join(lines)

    lines.append(f"Path valid: {len(deduped)} cells, start->goal, no walls")
    lines.append("RESULT: PASS")
    return "PASS", "\n".join(lines)


# ---------------------------------------------------------------------------
# Knowledge accumulation (the part that makes this harness different)
# ---------------------------------------------------------------------------
def extract_facts_from_verify(trajectory: List[dict]) -> Tuple[set, set]:
    """Harvest confirmed-wall and confirmed-open cells from every round's
    verify log. Matches legacy regex exactly so the summarizer sees the
    same fact set the legacy harness fed it."""
    known_walls: set = set()
    known_open: set = set()
    for t in trajectory:
        vlog = t.get("verify_log", "")
        for line in vlog.split("\n"):
            m = re.search(r"grid\((\d+),(\d+)\)\s+(WALL|open)", line)
            if m:
                r, c = int(m.group(1)), int(m.group(2))
                if m.group(3) == "WALL":
                    known_walls.add((r, c))
                else:
                    known_open.add((r, c))
    return known_walls, known_open


def format_partial_map(known_walls: set, known_open: set,
                       grid_rows: int, grid_cols: int) -> str:
    lines: List[str] = []
    for r in range(grid_rows):
        row = ""
        for c in range(grid_cols):
            if (r, c) in known_walls:
                row += "#"
            elif (r, c) in known_open:
                row += "."
            else:
                row += "?"
        lines.append(f"  {r:2d}: {row}")
    return "\n".join(lines)


def load_knowledge(workspace: Path) -> str:
    p = workspace / "knowledge.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def update_knowledge(
    vlm: Any,
    workspace: Path,
    trajectory: List[dict],
    grid_rows: int,
    grid_cols: int,
) -> str:
    """Re-summarize ``knowledge.md`` via an extra LLM turn. This is the
    defining move of this harness — memory is agent-authored natural
    language, rewritten round-by-round from the verify log + the
    agent's own thought. A deterministic fallback is written if the
    summarizer call fails."""
    knowledge_path = workspace / "knowledge.md"
    prev = knowledge_path.read_text(encoding="utf-8") if knowledge_path.exists() else ""

    known_walls, known_open = extract_facts_from_verify(trajectory)
    partial_map = format_partial_map(known_walls, known_open, grid_rows, grid_cols)

    last = trajectory[-1]
    last_thought = last.get("thought", "")
    last_verify = last.get("verify_log", "")

    prompt = (
        f"You are maintaining a knowledge document for a maze-solving agent.\n"
        f"The maze is a {grid_rows}x{grid_cols} grid. The agent is trying to find a path.\n\n"
        f"**CRITICAL: All coordinates are 0-indexed. Row 0 = top border, col 0 = left border. "
        f"The agent uses my_grid[r][c] directly — do NOT label coordinates as 1-indexed.**\n\n"
        f"## Previous knowledge\n{prev or '(none yet)'}\n\n"
        f"## Verified facts (from all rounds so far, 0-indexed)\n"
        f"Known walls ({len(known_walls)}): {sorted(known_walls)}\n"
        f"Known passages ({len(known_open)}): {sorted(known_open)}\n"
        f"Partial map (row 0 at top, 0-indexed):\n{partial_map}\n\n"
        f"## Latest round (R{last['round']})\n"
        f"Result: {last.get('verify_result') or last.get('error') or '?'}\n"
        f"Verify log:\n{last_verify}\n"
        f"Agent's reasoning:\n{last_thought[:500]}\n\n"
        f"## Your task\n"
        f"Write an updated knowledge document that:\n"
        f"1. Lists ALL confirmed wall cells and ALL confirmed passage cells (0-indexed)\n"
        f"2. Shows the updated partial map (0-indexed row numbers)\n"
        f"3. Notes which areas are still unexplored\n"
        f"4. Suggests which direction to explore next\n"
        f"5. Lists specific mistakes from past rounds to avoid repeating\n"
        f"6. **Reminds the agent: all coordinates are 0-indexed, use my_grid[r][c] directly, do NOT subtract 1**\n\n"
        f"Be concise but complete. This document will be the agent's PRIMARY reference next round."
    )

    try:
        knowledge_text = vlm.chat(
            [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            max_tokens=8192,
        )
    except Exception:  # noqa: BLE001
        knowledge_text = ""

    if not knowledge_text:
        knowledge_text = (
            f"# Maze Knowledge (fallback — summarizer call failed)\n\n"
            f"## Confirmed walls ({len(known_walls)})\n{sorted(known_walls)}\n\n"
            f"## Confirmed passages ({len(known_open)})\n{sorted(known_open)}\n\n"
            f"## Partial map\n{partial_map}\n"
        )

    knowledge_path.write_text(knowledge_text, encoding="utf-8")
    return knowledge_text


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_THOUGHT_RE = re.compile(r"^(.*?)```", re.DOTALL)


def extract_thought(response: str) -> str:
    m = _THOUGHT_RE.match(response)
    return m.group(1).strip() if m else response.strip()


def extract_agent_code(response: str) -> Optional[str]:
    m = _CODE_RE.search(response)
    return m.group(1).strip() if m else None


__all__ = [
    "HERE", "TASK_ROOT",
    "SETUP_OBSERVE_CODE", "SCENE_PREFIX", "SCENE_SUFFIX",
    "assemble_scene", "patch_observe_code",
    "verify_waypoints",
    "extract_facts_from_verify", "format_partial_map",
    "load_knowledge", "update_knowledge",
    "extract_thought", "extract_agent_code",
]
