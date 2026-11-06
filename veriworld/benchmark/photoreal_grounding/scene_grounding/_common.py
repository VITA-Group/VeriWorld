"""Shared building blocks for scene_ground harnesses.

Pipeline:
  1. ``OpenLevel`` to one of 4 v2 photoreal levels (living_room / office /
     forest / aiue). Print ``LEVEL_LOADING`` BEFORE the OpenLevel call —
     OpenLevel invalidates the running script's world handle, so any
     post-print may not arrive (lightwar memo).
  2. After level switch: lift the pawn to the level's ``z_offset_cm`` and
     wait ``warmup_s`` so GI / lumen settles before any screenshot is
     taken (office / forest are dark / black-screen otherwise).
  3. Sample a deterministic target pose from ``view_seed = hash(level, seed)``,
     teleport pawn there, take target screenshot.
  4. Teleport pawn back to ``spawn_pose``, run agent for ``max_rounds``
     walk/turn commands via ``submit_walk_cmd`` (tick-task in walk_tick.py).
  5. PASS when final pose is within ``tolerance_xy_cm`` and
     ``tolerance_yaw_deg`` of the target.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import random
import re
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).parent
log = logging.getLogger(__name__)

GROUNDTRUTH_PATH = HERE / "groundtruth.json"


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# UE-side python_exec snippets
# ---------------------------------------------------------------------------
def open_level_code(ue_path: str, short_name: str) -> str:
    """Return python_exec source that:
      - prints ``CUR_LEVEL <name>`` so caller knows current level
      - prints ``LEVEL_READY`` if already there, else ``LEVEL_LOADING``
        BEFORE firing OpenLevel (post-OpenLevel prints may not return).
    Caller waits 8s on LOADING, then re-runs a confirm snippet.
    """
    return (
        "import unreal_runtime as ur\n"
        f'cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
        f'print(f"CUR_LEVEL {{cur}}")\n'
        f'if cur == "{short_name}":\n'
        '    print("LEVEL_READY")\n'
        'else:\n'
        '    print("LEVEL_LOADING")\n'
        f'    ur.Engine.GameplayStatics.OpenLevel("{ue_path}", True, "")\n'
    )


CONFIRM_LEVEL_CODE = (
    "import unreal_runtime as ur\n"
    'cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
    'print(f"CUR_LEVEL {cur}")\n'
)


def lift_pawn_code(z_target_cm: float) -> str:
    """Set pawn Z to ``z_target_cm`` (absolute world Z). Used after level
    switch when pawn defaults underground (office / forest)."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "p = pawns[0]\n"
        "loc = p.K2_GetActorLocation()\n"
        "v = ur.CoreUObject.Vector(); v.X=loc.X; v.Y=loc.Y; v.Z="
        + f"{z_target_cm}\n"
        "p.K2_SetActorLocation(v, False)\n"
        "out = p.K2_GetActorLocation()\n"
        "print(f'PAWN_LIFTED {out.X:.1f} {out.Y:.1f} {out.Z:.1f}')\n"
    )


def sweep_pawn_to_code(x: float, y: float, z: float,
                       yaw: float, pitch: float = 0.0) -> str:
    """Move pawn TOWARD (x, y, z) with collision (sweep=True). If the
    straight-line path hits a wall, the pawn STOPS at the collision
    point — which is exactly what a "teleport" inside a real room
    should do (no wall-crossing). Sets ControlRotation regardless of
    whether translation succeeded.

    Prints:
      ``MOVED <reached?> <final_x> <final_y> <final_z> <yaw> <pitch>``
    where reached is 1 if the destination was reached, 0 if the move
    was clipped by a wall / floor / ceiling.
    """
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "p = pawns[0]\n"
        "v = ur.CoreUObject.Vector(); "
        f"v.X={x}; v.Y={y}; v.Z={z}\n"
        "reached = bool(p.K2_SetActorLocation(v, True))\n"
        "ctrl = p.GetController()\n"
        "r = ur.CoreUObject.Rotator(); "
        f"r.Pitch={pitch}; r.Yaw={yaw}; r.Roll=0\n"
        "ctrl.SetControlRotation(r)\n"
        "loc = p.K2_GetActorLocation()\n"
        "rot = ctrl.GetControlRotation()\n"
        "print(f'MOVED {int(reached)} {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
        "{rot.Yaw:.2f} {rot.Pitch:.2f}')\n"
    )


def step_forward_code(distance_cm: float, *, sign: float = 1.0,
                      step_cm: float = 10.0) -> str:
    """Walk pawn forward (or backward if ``sign=-1``) along the
    horizontal projection of its ControlRotation by ``distance_cm``,
    in ``step_cm``-sized sub-segments. Each substep is
    ``K2_SetActorLocation(v, sweep=True)`` — collision-aware: the
    first substep that's blocked stops the loop early. Z is pinned to
    the pawn's starting Z each substep so we don't drift into the
    floor / ceiling on a slope.

    Output: ``MOVED <reached> <X> <Y> <Z> <Yaw> <Pitch> <dist_moved>``
    where ``reached`` is 1 if the full distance was achieved, 0 if a
    wall stopped us short. ``dist_moved`` is the actual horizontal
    cm covered.
    """
    return (
        "import unreal_runtime as ur\n"
        "import math\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "p = pawns[0]\n"
        "ctrl = p.GetController()\n"
        "rot = ctrl.GetControlRotation()\n"
        "yaw_rad = math.radians(rot.Yaw)\n"
        f"sign = {sign}\n"
        f"target_dist = {distance_cm}\n"
        f"step = {step_cm}\n"
        "fwd_x = math.cos(yaw_rad) * sign\n"
        "fwd_y = math.sin(yaw_rad) * sign\n"
        "moved = 0.0\n"
        "while moved < target_dist:\n"
        "    chunk = min(step, target_dist - moved)\n"
        "    cur = p.K2_GetActorLocation()\n"
        "    nx = cur.X + fwd_x * chunk\n"
        "    ny = cur.Y + fwd_y * chunk\n"
        # Pin Z to current Z so a chunk doesn't drift the pawn into
        # the floor / ceiling on a slope. The maze + apartment levels
        # are mostly flat, but slope-resilient by default is cheap.
        "    v = ur.CoreUObject.Vector(); v.X=nx; v.Y=ny; v.Z=cur.Z\n"
        "    ok = bool(p.K2_SetActorLocation(v, True))\n"
        "    new = p.K2_GetActorLocation()\n"
        "    actual = math.sqrt((new.X-cur.X)**2 + (new.Y-cur.Y)**2)\n"
        "    moved += actual\n"
        # Stop if K2_SetActorLocation reported blocked AND we barely
        # advanced. A sliding-along-wall sub-step can return ok=False
        # while still moving ~the full chunk, in which case we should
        # keep going. Only break if both conditions hold or we made
        # essentially no progress.
        "    if (not ok and actual < chunk * 0.7) or actual < chunk * 0.2:\n"
        "        break\n"
        "loc = p.K2_GetActorLocation()\n"
        "rot = ctrl.GetControlRotation()\n"
        # ``reached`` reflects whether we actually covered the requested
        # distance, regardless of mid-loop sweep flags. 5 cm slop
        # tolerates the chunk-step rounding.
        "blocked = moved < target_dist - 5.0\n"
        "print(f'MOVED {int(not blocked)} {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
        "{rot.Yaw:.2f} {rot.Pitch:.2f} {moved:.1f}')\n"
    )


def turn_yaw_code(delta_deg: float) -> str:
    """Add ``delta_deg`` to ControlRotation.Yaw. Positive = clockwise
    (right), negative = counter-clockwise (left)."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "ctrl = pawns[0].GetController()\n"
        "rot = ctrl.GetControlRotation()\n"
        "new_rot = ur.CoreUObject.Rotator(); "
        f"new_rot.Pitch=rot.Pitch; new_rot.Yaw=(rot.Yaw + {delta_deg}) % 360.0; "
        "new_rot.Roll=0\n"
        "ctrl.SetControlRotation(new_rot)\n"
        "loc = pawns[0].K2_GetActorLocation()\n"
        "rot2 = ctrl.GetControlRotation()\n"
        "print(f'MOVED 1 {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
        "{rot2.Yaw:.2f} {rot2.Pitch:.2f} 0.0')\n"
    )


def look_pitch_code(pitch_deg: float) -> str:
    """Set absolute ControlRotation.Pitch (negative = look down)."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "ctrl = pawns[0].GetController()\n"
        "rot = ctrl.GetControlRotation()\n"
        "new_rot = ur.CoreUObject.Rotator(); "
        f"new_rot.Pitch={pitch_deg}; new_rot.Yaw=rot.Yaw; new_rot.Roll=0\n"
        "ctrl.SetControlRotation(new_rot)\n"
        "loc = pawns[0].K2_GetActorLocation()\n"
        "rot2 = ctrl.GetControlRotation()\n"
        "print(f'MOVED 1 {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
        "{rot2.Yaw:.2f} {rot2.Pitch:.2f} 0.0')\n"
    )


def parse_moved(stdout: str) -> Optional[dict]:
    """Parse ``MOVED <reached> <X> <Y> <Z> <Yaw> <Pitch> [<dist_moved>]``.
    Returns dict with ``reached`` (bool), pose keys, and (when emitted
    by ``step_forward_code``) ``dist_moved`` cm — None if no MOVED."""
    for line in stdout.splitlines():
        if line.startswith("MOVED "):
            parts = line.split()
            if len(parts) >= 7:
                d = {
                    "reached": parts[1] == "1",
                    "X": float(parts[2]), "Y": float(parts[3]),
                    "Z": float(parts[4]),
                    "Yaw":   float(parts[5]),
                    "Pitch": float(parts[6]),
                }
                if len(parts) >= 8:
                    d["dist_moved"] = float(parts[7])
                return d
    return None


def teleport_pawn_code(x: float, y: float, z: float,
                       yaw: float, pitch: float = 0.0) -> str:
    """Snap pawn to (x, y, z) + set camera control rotation. Prints
    ``POSE x y z yaw pitch`` on success.

    NOTE: this uses ``sweep=False`` — the pawn ignores collision and
    lands EXACTLY at (x, y, z). Use this ONLY for harness-internal
    placements at known-walkable poses (level spawn, anchor poses
    captured by manual WASD pilot). For agent actions where the
    straight-line path may cross walls, use
    :func:`sweep_pawn_to_code` instead so the move clips at the
    first wall."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "p = pawns[0]\n"
        "v = ur.CoreUObject.Vector(); "
        f"v.X={x}; v.Y={y}; v.Z={z}\n"
        "p.K2_SetActorLocation(v, False)\n"
        "ctrl = p.GetController()\n"
        "r = ur.CoreUObject.Rotator(); "
        f"r.Pitch={pitch}; r.Yaw={yaw}; r.Roll=0\n"
        "ctrl.SetControlRotation(r)\n"
        "loc = p.K2_GetActorLocation()\n"
        "rot = ctrl.GetControlRotation()\n"
        "print(f'POSE {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
        "{rot.Yaw:.2f} {rot.Pitch:.2f}')\n"
    )


GET_POSE_CODE = (
    "import unreal_runtime as ur\n"
    "world = ur.Engine.GetDefaultWorld()\n"
    "gm = ur.Engine.GetGameMode()\n"
    "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
    "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
    "p = pawns[0]\n"
    "loc = p.K2_GetActorLocation()\n"
    "rot = p.GetController().GetControlRotation()\n"
    "print(f'POSE {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f} "
    "{rot.Yaw:.2f} {rot.Pitch:.2f}')\n"
)


WALK_TICK_CODE = (HERE / "walk_tick.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Apartment doors (living_room only) — open BP_Door_01 + BP_Door_02 so the
# bathroom and bedroom are reachable from the living room hallway. The
# default apartment level loads with both doors closed; without this the
# walk-derived target/spawn are confined to the living-room/kitchen area
# and miss half the floorplan.
#
# Pattern verified in
# ${UE_BUILD_ROOT}/Plugins/UELivePy/Tests/
#   python/api/examples/apartment/14_open_door_02.py — calling
# ``a.Door_Timeline.PlayFromStart()`` on each door triggers the open
# animation. Idempotent: if already open the timeline replays harmlessly.
# ---------------------------------------------------------------------------
OPEN_APARTMENT_DOORS_CODE = (
    "import unreal_runtime as ur\n"
    "world = ur.Engine.GetDefaultWorld()\n"
    "doors_opened = []\n"
    'for a in world.GetActorsOfClass(ur.Engine.Actor):\n'
    "    try:\n"
    "        lbl = a.GetActorLabel()\n"
    '        if lbl in ("BP_Door_01", "BP_Door_02"):\n'
    "            tl = a.Door_Timeline\n"
    "            tl.PlayFromStart()\n"
    "            doors_opened.append(lbl)\n"
    "    except Exception as e:\n"
    "        pass\n"
    "print(f'DOORS_OPENED {doors_opened}')\n"
)


# ---------------------------------------------------------------------------
# Walk-tick command queue
# ---------------------------------------------------------------------------
def _set_walk_cmd_code(cmd_dict: dict) -> str:
    return (
        "import builtins, json\n"
        "builtins._WALK_DONE = False\n"
        "builtins._WALK_LOG = None\n"
        f"builtins._WALK_CMD = json.loads(r'''{json.dumps(cmd_dict)}''')\n"
        "print('CMD_QUEUED')\n"
    )


_POLL_DONE_CODE = (
    "import builtins, json\n"
    "done = bool(getattr(builtins, '_WALK_DONE', False))\n"
    "log = getattr(builtins, '_WALK_LOG', None) or {}\n"
    "print('WALK_RESULT:' + json.dumps({'done': done, 'log': log}))\n"
)


async def submit_walk_cmd(engine, cmd: dict, *,
                          poll_interval: float = 0.25,
                          timeout: float = 30.0) -> Optional[dict]:
    """Push one command to walk_tick and poll until _WALK_DONE.
    Returns the {cmd, from_x..to_x, yaw, pitch, blocked, dist_moved}
    log entry, or None on timeout."""
    await engine.python_exec(_set_walk_cmd_code(cmd))
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        out = unwrap(await engine.python_exec(_POLL_DONE_CODE))
        for line in out.splitlines():
            if line.startswith("WALK_RESULT:"):
                try:
                    data = json.loads(line[len("WALK_RESULT:"):])
                    if data.get("done"):
                        return data.get("log") or {}
                except Exception:
                    pass
        await asyncio.sleep(poll_interval)
    log.warning("submit_walk_cmd timeout: cmd=%s", cmd)
    return None


# ---------------------------------------------------------------------------
# Stdout / pose parsing
# ---------------------------------------------------------------------------
def unwrap(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


_POSE_RE = re.compile(
    r"POSE\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"
)


def parse_pose(stdout: str) -> Optional[dict]:
    for line in stdout.splitlines():
        m = _POSE_RE.search(line)
        if m:
            x, y, z, yaw, pitch = m.groups()
            return {"X": float(x), "Y": float(y), "Z": float(z),
                    "Yaw": float(yaw), "Pitch": float(pitch)}
    return None


def parse_cur_level(stdout: str) -> Optional[str]:
    for line in stdout.splitlines():
        if line.startswith("CUR_LEVEL "):
            return line[len("CUR_LEVEL "):].strip()
    return None


# ---------------------------------------------------------------------------
# Target pose sampling (deterministic per (level, seed))
# ---------------------------------------------------------------------------
def view_seed_for(level_id: str, seed: int) -> int:
    """Stable hash of (level, seed) → 31-bit positive int. Same view_seed
    yields same target pose across all models / harness restarts.

    Uses ``hashlib.sha256`` rather than Python's builtin ``hash()``
    because the latter is randomized per Python process by default
    (``PYTHONHASHSEED``), so identical ``(level, seed)`` would produce
    different view_seeds across separate invocations — silently
    breaking cross-model reproducibility. SHA-256 is purely a function
    of the bytes, no process state."""
    import hashlib
    digest = hashlib.sha256(f"{level_id}|{seed}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def sample_target_pose(level_meta: dict, view_seed: int) -> dict:
    """Uniform-random target XY inside the level's sampling_box, with
    deterministic yaw. Z is FIXED to the level's spawn_pose.Z (canonical
    eye level) — sampling Z freely tended to drop the pawn slightly
    inside / above furniture, where CharacterMovement.AddMovementInput
    can't propel it (walk-derivation then fails to leave the spot)."""
    rng = random.Random(view_seed)
    box = level_meta["sampling_box"]
    yaw_lo, yaw_hi = level_meta["sampling_yaw_deg"]
    return {
        "X": rng.uniform(*box["X"]),
        "Y": rng.uniform(*box["Y"]),
        "Z": float(level_meta["spawn_pose"]["Z"]),
        "Yaw": rng.uniform(yaw_lo, yaw_hi),
        "Pitch": 0.0,
    }


def _anchor_to_pose(anchor: dict, jitter_yaw_deg: float, rng) -> dict:
    """Convert an anchor entry (with optional ``_label``) to a pose
    dict the rest of the pipeline expects, applying a small random
    yaw jitter so identical anchors don't always produce identical
    targets across seeds."""
    yaw = float(anchor["Yaw"]) + rng.uniform(-jitter_yaw_deg, jitter_yaw_deg)
    return {
        "X": float(anchor["X"]),
        "Y": float(anchor["Y"]),
        "Z": float(anchor["Z"]),
        "Yaw":   yaw % 360.0,
        "Pitch": float(anchor.get("Pitch", 0.0)),
    }


async def _random_walk_spawn_from_target(
        engine, target_pose: dict, rng: random.Random,
        *, min_drift_cm: float = 130.0,
        max_steps: int = 12,
        step_cm_min: float = 100.0, step_cm_max: float = 200.0,
        yaw_jitter_max_deg: float = 120.0) -> dict:
    """Teleport pawn to ``target_pose``, then run (random-yaw turn,
    forward) cycles with sweep collision so the pawn drifts a short
    distance INSIDE THE SAME ROOM (walls clip the walk if a step would
    cross them). Loops until the horizontal drift from target reaches
    ``min_drift_cm`` OR ``max_steps`` is exhausted — guarantees the
    spawn is outside the pass tolerance so the task is non-trivial,
    even in tight rooms (bathroom) where most random forwards hit
    walls. Returns the resulting pose; yaw is rotated to face back
    toward the target so the agent's first screenshot already includes
    target landmarks for orientation.

    Determinism: same ``rng`` instance + same level → same yaw / step
    sequence → same physical outcome → same loop exit step → same
    spawn. View_seed is stable across all models for clean
    apples-to-apples."""
    # 1. Snap pawn to target.
    await engine.python_exec(teleport_pawn_code(
        target_pose["X"], target_pose["Y"], target_pose["Z"],
        target_pose["Yaw"], target_pose.get("Pitch", 0.0)))
    await asyncio.sleep(0.15)

    tx, ty = float(target_pose["X"]), float(target_pose["Y"])

    # 2. Random walk loop. Exit early once we've drifted far enough.
    cur_pose: Optional[dict] = None
    for _ in range(max_steps):
        dyaw = rng.uniform(-yaw_jitter_max_deg, yaw_jitter_max_deg)
        await engine.python_exec(turn_yaw_code(dyaw))
        step = rng.uniform(step_cm_min, step_cm_max)
        await engine.python_exec(step_forward_code(step, sign=1.0))

        out = unwrap(await engine.python_exec(GET_POSE_CODE))
        cur_pose = parse_pose(out)
        if cur_pose is None:
            continue
        d = math.sqrt((cur_pose["X"] - tx) ** 2 + (cur_pose["Y"] - ty) ** 2)
        if d >= min_drift_cm:
            break

    # 3. Final pose; if parse failed throughout, fallback to target.
    pose = cur_pose if cur_pose is not None else dict(target_pose)
    final_drift = math.sqrt((pose["X"] - tx) ** 2 + (pose["Y"] - ty) ** 2)
    if final_drift < min_drift_cm:
        log.warning("random walk only drifted %.0f cm < %.0f cm in %d steps "
                    "(room may be too tight); accepting trivial spawn",
                    final_drift, min_drift_cm, max_steps)

    # 4. Spawn faces back toward target.
    dx = target_pose["X"] - pose["X"]
    dy = target_pose["Y"] - pose["Y"]
    if abs(dx) + abs(dy) > 1e-3:
        pose["Yaw"] = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    pose["Pitch"] = 0.0
    return pose


async def _anchor_walked_target_and_spawn(engine, level_meta: dict,
                                           view_seed: int,
                                           anchors: list) -> tuple:
    """Pick ONE anchor as the target (rng-determined from view_seed)
    and derive spawn by collision-aware random walk from the target.
    Same view_seed → identical (target, spawn) across all models."""
    rng = random.Random(view_seed)
    a = rng.randrange(len(anchors))
    target_anchor = anchors[a]
    target_pose = _anchor_to_pose(target_anchor, jitter_yaw_deg=15.0, rng=rng)
    spawn_pose  = await _random_walk_spawn_from_target(
        engine, target_pose, rng)
    dist = math.sqrt((spawn_pose["X"] - target_pose["X"]) ** 2
                     + (spawn_pose["Y"] - target_pose["Y"]) ** 2)
    log.info("anchor target=%r (%.0f,%.0f,%.0f yaw=%.0f)  "
             "spawn (walked from target) -> (%.0f,%.0f,%.0f yaw=%.0f)  "
             "spawn-to-target dist=%.0f cm",
             target_anchor.get("_label", f"#{a}"),
             target_pose["X"], target_pose["Y"], target_pose["Z"], target_pose["Yaw"],
             spawn_pose["X"], spawn_pose["Y"], spawn_pose["Z"], spawn_pose["Yaw"],
             dist)
    return target_pose, spawn_pose, 1


async def sample_walkable_target_and_spawn(
        engine, level_meta: dict, view_seed: int,
        *, min_walk_distance_cm: float = 200.0,
        max_target_attempts: int = 5,
) -> tuple:
    """Pick (target_pose, spawn_pose) for a level + seed.

    Two paths:

    (A) ANCHOR mode (preferred). If ``level_meta["target_anchors"]``
    is set (a list of hand-picked walkable poses captured by manual
    WASD pilot), the function selects TWO distinct anchors per
    ``view_seed``:
      - target_pose = anchor_a + small yaw jitter
      - spawn_pose  = anchor_b + small yaw jitter
    No walking required for setup; both poses are by construction
    walkable (the human visited them). This sidesteps the
    ``FloatingPawnMovement`` issue where AddMovementInput barely
    advances the pawn. Returns (target, spawn, attempts=1).

    (B) WALK fallback. If a level has no anchors, fall back to the
    walk-only derivation: teleport pawn to spawn_pose, walk to
    target, walk further to spawn. Used only as a last resort —
    walks currently return ~2 cm per call due to a separate
    walk_tick / FloatingPawnMovement bug.

    REQUIREMENT (both modes): walk_tick task must already be loaded.
    Returns (target_pose, spawn_pose, attempts_used) — same
    signature as before.
    """
    anchors = level_meta.get("target_anchors") or []
    if anchors:
        return await _anchor_walked_target_and_spawn(
            engine, level_meta, view_seed, anchors)
    log.warning("level has 0 anchors; falling back to walk-only derivation "
                "— walk_tick currently returns tiny distances")
    rng = random.Random(view_seed)
    base_spawn = level_meta["spawn_pose"]
    z = float(base_spawn["Z"])
    # Per-attempt counts kept conservative: 4 walks of ~150 cm each =
    # ~600 cm max → covers a typical apartment room without sailing
    # through to the next one.
    n_target_walks = 4
    n_spawn_walks  = 3
    walk_distance_cm = 150.0

    last_target = None
    last_spawn  = None
    last_dist   = 0.0
    for attempt in range(max_target_attempts):
        # ---- Step 1: teleport to known-good spawn_pose ----
        target_walk_yaw = rng.uniform(0.0, 360.0)
        await engine.python_exec(teleport_pawn_code(
            float(base_spawn["X"]), float(base_spawn["Y"]), z,
            target_walk_yaw, 0.0))
        await asyncio.sleep(0.2)

        # ---- Step 2: walk to TARGET ----
        for _ in range(n_target_walks):
            await submit_walk_cmd(engine, {"cmd": "walk",
                                            "distance": walk_distance_cm})
        target_out = unwrap(await engine.python_exec(GET_POSE_CODE))
        target_pose = parse_pose(target_out)
        if target_pose is None:
            log.warning("attempt %d: target pose parse failed; retrying",
                        attempt + 1)
            continue
        # Random target FACING yaw — independent of the walk yaw so the
        # camera at the target isn't always staring at the path it came
        # from.
        target_pose["Yaw"]   = rng.uniform(0.0, 360.0)
        target_pose["Pitch"] = 0.0
        log.info("attempt %d: target via walk(yaw=%.0f) -> (%.0f,%.0f,%.0f) yaw=%.0f",
                 attempt + 1, target_walk_yaw,
                 target_pose["X"], target_pose["Y"], target_pose["Z"],
                 target_pose["Yaw"])

        # ---- Step 3: walk further to SPAWN (NO teleport) ----
        # Set facing to a "walk-away" yaw via SetControlRotation only;
        # actor location stays at target after the GET_POSE above.
        spawn_walk_yaw = (target_walk_yaw + rng.uniform(120.0, 240.0)
                          * rng.choice((-1.0, 1.0))) % 360.0
        await engine.python_exec(set_control_rotation_code(
            spawn_walk_yaw, 0.0))
        await asyncio.sleep(0.1)
        for _ in range(n_spawn_walks):
            await submit_walk_cmd(engine, {"cmd": "walk",
                                            "distance": walk_distance_cm})
        spawn_out = unwrap(await engine.python_exec(GET_POSE_CODE))
        spawn = parse_pose(spawn_out)
        if spawn is None:
            log.warning("attempt %d: spawn pose parse failed; retrying",
                        attempt + 1)
            continue
        # Spawn yaw faces back toward the target (so the agent's first
        # screenshot already includes some of the room context they
        # need to recover).
        spawn["Yaw"]   = (math.degrees(math.atan2(
            target_pose["Y"] - spawn["Y"],
            target_pose["X"] - spawn["X"])) + 360.0) % 360.0
        spawn["Pitch"] = 0.0

        d = math.sqrt((spawn["X"] - target_pose["X"]) ** 2
                      + (spawn["Y"] - target_pose["Y"]) ** 2)
        log.info("attempt %d: spawn via walk(yaw=%.0f) -> (%.0f,%.0f) "
                 "yaw=%.0f  spawn-to-target dist=%.0f cm",
                 attempt + 1, spawn_walk_yaw,
                 spawn["X"], spawn["Y"], spawn["Yaw"], d)
        last_target, last_spawn, last_dist = target_pose, spawn, d
        if d >= min_walk_distance_cm:
            log.info("attempt %d accepted (dist %.0f >= %d cm)",
                     attempt + 1, d, int(min_walk_distance_cm))
            return target_pose, spawn, attempt + 1

    log.warning("sample_walkable_target_and_spawn: %d attempts, best "
                "dist %.0f cm < %d cm; using last (target, spawn)",
                max_target_attempts, last_dist, int(min_walk_distance_cm))
    return last_target, last_spawn, max_target_attempts


def set_control_rotation_code(yaw: float, pitch: float = 0.0) -> str:
    """Set ONLY the camera ControlRotation, leaving actor location
    untouched. Used between target-walk and spawn-walk so we change
    facing without K2_SetActorLocation (which would teleport)."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if not pawns: print('PAWN_MISSING'); raise SystemExit\n"
        "ctrl = pawns[0].GetController()\n"
        "r = ur.CoreUObject.Rotator(); "
        f"r.Pitch={pitch}; r.Yaw={yaw}; r.Roll=0\n"
        "ctrl.SetControlRotation(r)\n"
        "rot = ctrl.GetControlRotation()\n"
        "print(f'ROT {rot.Yaw:.2f} {rot.Pitch:.2f}')\n"
    )


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------
def _yaw_delta_deg(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def verify_pose_match(target: dict, agent: dict,
                      tol_xy_cm: float, tol_yaw_deg: float,
                      tol_pitch_deg: Optional[float] = None) -> dict:
    """Three-axis pose match: XY position + yaw + (optionally) pitch.

    The view-recovery task is "stand where the target shot was taken
    AND face the same way" — so all three of (xy, yaw, pitch) must
    match. Pitch was previously ignored, which let trivially-misframed
    poses pass when target had a non-zero pitch (e.g. looking slightly
    down at the floor). Pass ``tol_pitch_deg=None`` to skip the pitch
    check (back-compat for callers that don't supply it)."""
    dx = target["X"] - agent["X"]
    dy = target["Y"] - agent["Y"]
    dist = math.sqrt(dx * dx + dy * dy)
    dyaw = _yaw_delta_deg(target["Yaw"], agent["Yaw"])
    dpitch = abs(float(target.get("Pitch", 0.0)) - float(agent.get("Pitch", 0.0)))
    passed = (dist <= tol_xy_cm) and (dyaw <= tol_yaw_deg)
    if tol_pitch_deg is not None:
        passed = passed and (dpitch <= tol_pitch_deg)
    if passed:
        reason = "ok"
    elif dist > tol_xy_cm:
        reason = f"xy {dist:.0f}cm > {tol_xy_cm:.0f}cm"
    elif dyaw > tol_yaw_deg:
        reason = f"yaw {dyaw:.1f}\N{DEGREE SIGN} > {tol_yaw_deg:.1f}\N{DEGREE SIGN}"
    else:
        reason = f"pitch {dpitch:.1f}\N{DEGREE SIGN} > {tol_pitch_deg:.1f}\N{DEGREE SIGN}"
    return {
        "passed": passed,
        "xy_distance_cm": round(dist, 2),
        "yaw_delta_deg": round(dyaw, 2),
        "pitch_delta_deg": round(dpitch, 2),
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------
PACKAGE_SCREENSHOT_DIR = Path(
    os.environ.get(
        "SCENE_GROUND_SCREENSHOT_DIR",
        r"${UE_BUILD_ROOT}\PackagedOutput_v2"
        r"\Windows\demo1\Saved\Screenshots\Windows",
    )
)


async def take_screenshot(engine, name: str, workspace: Path,
                          screenshot_dir: Optional[Path] = None) -> Path:
    from veriworld.common.screenshot import capture_runtime_screenshot
    return await capture_runtime_screenshot(
        engine, name, workspace, screenshot_dir=screenshot_dir)


# ---------------------------------------------------------------------------
# Level-switch driver (handles LOADING vs READY + post-switch z-lift + warmup)
# ---------------------------------------------------------------------------
async def open_level_and_warmup(engine, level_meta: dict) -> None:
    """Idempotent level switch + pawn z-lift + GI warmup. Caller can
    invoke multiple times safely; if already on the level, just re-lifts
    + waits."""
    short = level_meta["short_name"]
    out = unwrap(await engine.python_exec(
        open_level_code(level_meta["ue_path"], short)))
    log.info("open_level: %s", out.strip().replace("\n", " | "))
    if "LEVEL_LOADING" in out:
        await asyncio.sleep(8.0)
        # OpenLevel invalidated the world handle — re-confirm via fresh exec.
        confirm = unwrap(await engine.python_exec(CONFIRM_LEVEL_CODE))
        cur = parse_cur_level(confirm)
        if cur != short:
            log.warning("level didn't settle to %s (got %r); waiting 4s more",
                        short, cur)
            await asyncio.sleep(4.0)
    else:
        await asyncio.sleep(1.5)
    # Lift pawn if level needs it (pawn defaults underground).
    z_off = level_meta.get("z_offset_cm", 0)
    if z_off > 0:
        spawn_z = level_meta["spawn_pose"]["Z"]
        out = unwrap(await engine.python_exec(lift_pawn_code(spawn_z)))
        log.info("lift_pawn: %s", out.strip())
    # Warmup for GI / lumen / shader compile.
    warmup = float(level_meta.get("warmup_s", 0.0))
    if warmup > 0:
        log.info("warmup %.1fs (level %s)", warmup, short)
        await asyncio.sleep(warmup)
    # Apartment-only: open BP_Door_01 + BP_Door_02 so the bathroom and
    # bedroom are reachable from the living room. The default level
    # loads with both doors closed; without this the walk-derived
    # target/spawn never exit the kitchen+living-room volume — and
    # walks immediately collide with closed doors, returning ~30 cm.
    # ``short_name`` is the UE level name as returned by
    # ``GetCurrentLevelName`` (e.g. "Living_room_day"), NOT the
    # CLI-facing alias "living_room".
    if short == "Living_room_day":
        out = unwrap(await engine.python_exec(OPEN_APARTMENT_DOORS_CODE))
        log.info("apartment doors: %s", out.strip())
        # Door_Timeline takes ~1 s to play to fully open; wait so the
        # downstream walk doesn't push against a half-open door.
        await asyncio.sleep(1.5)


# ---------------------------------------------------------------------------
# Response parsers (action JSON from VLM)
# ---------------------------------------------------------------------------
_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def extract_action(text: str) -> Optional[dict]:
    for m in _JSON_FENCE.finditer(text):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict) and "cmd" in d:
                return d
        except Exception:
            continue
    # Fallback: bare JSON object containing "cmd"
    for m in re.finditer(r"\{[^{}]*\"cmd\"[\s\S]*?\}", text):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and "cmd" in d:
                return d
        except Exception:
            continue
    return None


def extract_thought(text: str) -> str:
    return text.split("```", 1)[0].strip()


__all__ = [
    "HERE", "GROUNDTRUTH_PATH", "PACKAGE_SCREENSHOT_DIR",
    "load_groundtruth",
    "open_level_code", "CONFIRM_LEVEL_CODE", "lift_pawn_code",
    "teleport_pawn_code", "sweep_pawn_to_code", "set_control_rotation_code",
    "step_forward_code", "turn_yaw_code", "look_pitch_code",
    "parse_moved",
    "GET_POSE_CODE", "WALK_TICK_CODE",
    "submit_walk_cmd",
    "unwrap", "parse_pose", "parse_cur_level",
    "view_seed_for", "sample_target_pose",
    "sample_walkable_target_and_spawn",
    "verify_pose_match",
    "take_screenshot", "open_level_and_warmup",
    "extract_action", "extract_thought",
]
