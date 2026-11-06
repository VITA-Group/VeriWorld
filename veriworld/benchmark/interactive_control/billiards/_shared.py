"""Shared helpers for billiards harnesses (video / image variants).

Both harnesses use ``InteractiveEngine`` — a single UE instance per
seed, kept alive across all rounds. The scene is built once at
Round 0 and then only the cue's velocity is set per round; balls
never re-spawn, so end-of-round-N positions ARE start-of-round-(N+1)
positions automatically (no state.json round-trip needed).

This module owns the modality-agnostic pieces:

- ``SCENE_SETUP_CODE``: spawn table, rails, hole markers, cue + 5
  coloured balls, and the top-down camera. Run ONCE per seed.
- ``SHOT_TICK_CODE``: look up the cue actor by label, apply the
  agent's shot, run a settle-detect tick task, write
  ``log_for_verify.txt`` with the verdict.
- Response parsing helpers: ``extract_thought`` / ``extract_shot``.
- Verify-log parser: ``parse_verify_log``.

Recording / screenshot capture is handled by each harness's
``_common.py`` (the modality difference).

Adapted from the chaos debug script
``VeriWorld-benchmark/.../navigation/debug/chaos/billiards/01_billiards_basic.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


HERE = Path(__file__).parent
LABEL_PREFIX = "BILL_"


# ---------------------------------------------------------------------------
# UE-side scene code: ONE-TIME setup (Round 0)
# ---------------------------------------------------------------------------
SCENE_SETUP_CODE = r'''import unreal_runtime as ur
import json
import math
import os

BASE = r"__BASE__"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Geometry
TABLE_CENTER_XY = params["table_center_xy"]
TABLE_TOP_Z = params["table_top_z"]
TABLE_SIZE_X = params["table_size_x"]
TABLE_SIZE_Y = params["table_size_y"]
TABLE_THICKNESS = params["table_thickness"]
RAIL_HEIGHT = params["rail_height"]
RAIL_THICKNESS = params["rail_thickness"]
HOLE_CORNER_GAP = params["hole_corner_gap"]
BALL_RADIUS = params["ball_radius"]
CUE_LD = params.get("cue_linear_damping", params["ball_linear_damping"])
CUE_AD = params.get("cue_angular_damping", params["ball_angular_damping"])
BALL_LD = params["ball_linear_damping"]
BALL_AD = params["ball_angular_damping"]
BALLS_INIT = params["balls"]
# Per-difficulty camera height: hard preset uses a longer rectangular
# table and needs the camera raised to fit; standard preset omits the
# key entirely and falls through to 900 (byte-identical to pre-difficulty).
CAM_HEIGHT_CM = float(params.get("camera_height_cm", 900.0))
CAM_PITCH_DEG = -90.0
LABEL_PREFIX = "__LABEL__"


def _vec(x, y, z):
    v = ur.CoreUObject.Vector(); v.X = x; v.Y = y; v.Z = z; return v


_CUBE = None; _SPHERE = None; _CYL = None; _BASE_MAT = None


def _get_cube():
    global _CUBE
    if _CUBE is None:
        _CUBE = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
            "/Engine/BasicShapes/Cube.Cube")
    return _CUBE


def _get_sphere():
    global _SPHERE
    if _SPHERE is None:
        _SPHERE = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
            "/Engine/BasicShapes/Sphere.Sphere")
    return _SPHERE


def _get_cyl():
    global _CYL
    if _CYL is None:
        _CYL = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
            "/Engine/BasicShapes/Cylinder.Cylinder")
    return _CYL


def _apply_tint(comp, rgb):
    global _BASE_MAT
    if _BASE_MAT is None:
        _BASE_MAT = ur.StaticLoadObject(
            ur.Engine.MaterialInterface, None,
            "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
    if not _BASE_MAT: return
    comp.SetMaterial(0, _BASE_MAT)
    mid = comp.CreateDynamicMaterialInstance(0)
    col = ur.CoreUObject.LinearColor()
    col.R, col.G, col.B, col.A = rgb[0], rgb[1], rgb[2], 1.0
    mid.SetVectorParameterValue("Color", col)


def _identity_quat():
    return ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(0, 0, 0))


def _spawn_box(center_xyz, size_xyz, rgb, label, collides=True):
    world = ur.Engine.GetDefaultWorld()
    t = ur.CoreUObject.Transform()
    t.Translation = _vec(*center_xyz)
    t.Rotation = _identity_quat()
    sv = ur.CoreUObject.Vector()
    sv.X = size_xyz[0] / 100.0; sv.Y = size_xyz[1] / 100.0; sv.Z = size_xyz[2] / 100.0
    t.Scale3D = sv
    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    c = a.StaticMeshComponent
    c.SetMobility(2); c.SetStaticMesh(_get_cube())
    _apply_tint(c, rgb)
    if collides:
        c.SetCollisionEnabled(1); c.SetCollisionProfileName("BlockAll")
    else:
        c.SetCollisionEnabled(0)
    a.SetActorLabel(label)
    return a


def _spawn_ball(pos_xy, rgb, label, is_cue):
    world = ur.Engine.GetDefaultWorld()
    t = ur.CoreUObject.Transform()
    t.Translation = _vec(pos_xy[0], pos_xy[1], TABLE_TOP_Z + BALL_RADIUS + 0.5)
    t.Rotation = _identity_quat()
    sv = ur.CoreUObject.Vector()
    sv.X = sv.Y = sv.Z = (BALL_RADIUS * 2.0) / 100.0
    t.Scale3D = sv
    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    c = a.StaticMeshComponent
    c.SetMobility(2); c.SetStaticMesh(_get_sphere())
    _apply_tint(c, rgb)
    c.SetCollisionEnabled(1); c.SetCollisionProfileName("PhysicsActor")
    c.SetSimulatePhysics(True)
    if hasattr(c, "SetEnableGravity"): c.SetEnableGravity(True)
    if hasattr(c, "SetLinearDamping"):
        c.SetLinearDamping(CUE_LD if is_cue else BALL_LD)
    if hasattr(c, "SetAngularDamping"):
        c.SetAngularDamping(CUE_AD if is_cue else BALL_AD)
    a.SetActorLabel(label)
    return a


def _clean_prior():
    w = ur.Engine.GetDefaultWorld()
    for cls in (ur.Engine.StaticMeshActor, ur.Engine.TextRenderActor):
        for a in w.GetActorsOfClass(cls):
            try:
                if a.GetActorLabel().startswith(LABEL_PREFIX):
                    a.K2_DestroyActor()
            except Exception:
                pass


def _setup_camera():
    world = ur.Engine.GetDefaultWorld()
    game_mode = ur.Engine.GetGameMode()
    pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
    if not pawns: return
    pawn = pawns[0]; ctrl = pawn.GetController()
    cx, cy = TABLE_CENTER_XY
    pawn.K2_SetActorLocation(_vec(cx, cy, CAM_HEIGHT_CM), False)
    rot = ur.CoreUObject.Rotator()
    rot.Pitch = CAM_PITCH_DEG; rot.Yaw = 0.0; rot.Roll = 0.0
    ctrl.SetControlRotation(rot)


_clean_prior()
_setup_camera()

# Table
cx, cy = TABLE_CENTER_XY
_spawn_box((cx, cy, TABLE_TOP_Z - TABLE_THICKNESS / 2.0),
           (TABLE_SIZE_X, TABLE_SIZE_Y, TABLE_THICKNESS),
           (0.06, 0.34, 0.11), LABEL_PREFIX + "table")

# Rails (4 with corner gaps)
hx = TABLE_SIZE_X / 2.0; hy = TABLE_SIZE_Y / 2.0
gap = HOLE_CORNER_GAP; rz = TABLE_TOP_Z + RAIL_HEIGHT / 2.0
rail_len_x = TABLE_SIZE_X - 2 * gap
rail_len_y = TABLE_SIZE_Y - 2 * gap
wood = (0.52, 0.32, 0.14)
_spawn_box((cx, cy + hy + RAIL_THICKNESS / 2.0, rz),
           (rail_len_x, RAIL_THICKNESS, RAIL_HEIGHT), wood, LABEL_PREFIX + "rail_top")
_spawn_box((cx, cy - hy - RAIL_THICKNESS / 2.0, rz),
           (rail_len_x, RAIL_THICKNESS, RAIL_HEIGHT), wood, LABEL_PREFIX + "rail_bottom")
_spawn_box((cx + hx + RAIL_THICKNESS / 2.0, cy, rz),
           (RAIL_THICKNESS, rail_len_y, RAIL_HEIGHT), wood, LABEL_PREFIX + "rail_right")
_spawn_box((cx - hx - RAIL_THICKNESS / 2.0, cy, rz),
           (RAIL_THICKNESS, rail_len_y, RAIL_HEIGHT), wood, LABEL_PREFIX + "rail_left")

# Hole markers
hole_r = HOLE_CORNER_GAP * 0.45; inset = hole_r * 0.55
hole_z = TABLE_TOP_Z + 1.5
for name, hxi, hyi in (
    ("ne", cx + hx - inset, cy + hy - inset),
    ("nw", cx - hx + inset, cy + hy - inset),
    ("se", cx + hx - inset, cy - hy + inset),
    ("sw", cx - hx + inset, cy - hy + inset),
):
    world = ur.Engine.GetDefaultWorld()
    t = ur.CoreUObject.Transform()
    t.Translation = _vec(hxi, hyi, hole_z)
    t.Rotation = _identity_quat()
    sv = ur.CoreUObject.Vector()
    sv.X = hole_r / 50.0; sv.Y = hole_r / 50.0; sv.Z = 0.03
    t.Scale3D = sv
    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    c = a.StaticMeshComponent
    c.SetMobility(2); c.SetStaticMesh(_get_cyl())
    _apply_tint(c, (0.03, 0.03, 0.03))
    c.SetCollisionEnabled(0)
    a.SetActorLabel(LABEL_PREFIX + "hole_" + name)

# Balls
for b in BALLS_INIT:
    is_cue = (b["id"] == "cue")
    _spawn_ball(b["pos"], b["rgb"], LABEL_PREFIX + b["id"], is_cue)

print("[billiards] scene setup complete")

# Coloured-ball count for the initial verdict + state.json (must
# match params["num_target_balls"]; we recompute from BALLS_INIT here
# rather than read the param so this is robust to manual params.json
# edits during debugging).
N_TARGET_INITIAL = sum(1 for b in BALLS_INIT if b["id"] != "cue")

# Initial verdict so harness's harvest sees something coherent.
with open(LOG_PATH, "w", encoding="utf-8") as f:
    f.write(f"# alive_target={N_TARGET_INITIAL}\n# cue_pocketed=no\n# RESULT: SETUP\n")

# Initial state.json so harnesses that show ground-truth positions
# (visionposition, visiongroundtruth) have data for ROUND 1's prompt.
# Without this the agent's first shot has no position info because
# state.json is normally written by per-round _harvest, which only
# runs AFTER round 1's shot.
state_initial = {
    "alive_target": N_TARGET_INITIAL,
    "cue_scratched": False,
    "pocketed_this_round": [],
    "table_center_xy": list(TABLE_CENTER_XY),
    "table_size_x": TABLE_SIZE_X,
    "table_size_y": TABLE_SIZE_Y,
    "table_top_z": TABLE_TOP_Z,
    "ball_radius": BALL_RADIUS,
    "balls": {},
}
world = ur.Engine.GetDefaultWorld()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    lbl = a.GetActorLabel()
    if not lbl.startswith(LABEL_PREFIX): continue
    if lbl == LABEL_PREFIX + "cue":
        bid = "cue"
    elif lbl.startswith(LABEL_PREFIX + "ball_"):
        bid = lbl[len(LABEL_PREFIX):]
    else:
        continue
    p = a.K2_GetActorLocation()
    state_initial["balls"][bid] = [round(float(p.X), 1),
                                   round(float(p.Y), 1),
                                   round(float(p.Z), 1)]
state_path_initial = os.path.join(BASE, "state.json")
with open(state_path_initial, "w", encoding="utf-8") as f:
    json.dump(state_initial, f, indent=2)
print(f"[billiards] initial state.json written with {len(state_initial['balls'])} balls")
'''


# ---------------------------------------------------------------------------
# UE-side scene code: PER-ROUND shot + settle-detect + harvest
# ---------------------------------------------------------------------------
SHOT_TICK_CODE = r'''import unreal_runtime as ur
import json
import math
import os

BASE = r"__BASE__"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

shot = json.loads(r"""__SHOT_JSON__""")
RECORD = (r"__MODALITY__" == "video")

LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")
LABEL_PREFIX = "__LABEL__"
TABLE_TOP_Z = params["table_top_z"]
CUE_MAX_SPEED = params["cue_max_speed_cm_s"]

SETTLE_VEL_CM_S = 8.0           # tier 1: truly stopped
SETTLE_HOLD_S = 0.3
SETTLE_MIN_T = 0.8
# Per-difficulty overrides (omitted from params on standard for
# byte-identity); standard defaults match the pre-difficulty values.
SOFT_CAP_S = float(params.get("settle_soft_cap_s", 10.0))
HARD_CAP_FREEZE_VEL_CM_S = float(params.get("settle_hard_cap_freeze_vel_cm_s", 50.0))
EXTENDED_CAP_S = float(params.get("settle_extended_cap_s", 25.0))
RECORD_DURATION_S = EXTENDED_CAP_S  # back-compat name kept for the
                                    # tick-task max_duration arithmetic


def _vec(x, y, z):
    v = ur.CoreUObject.Vector(); v.X = x; v.Y = y; v.Z = z; return v


# Look up cue + all live balls by label.
world = ur.Engine.GetDefaultWorld()
cue_comp = None
ball_comps = []  # (id, comp) — id is "cue" or "ball_N"
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    lbl = a.GetActorLabel()
    if not lbl.startswith(LABEL_PREFIX): continue
    if lbl == LABEL_PREFIX + "cue":
        cue_comp = a.StaticMeshComponent
        ball_comps.append(("cue", cue_comp))
    elif lbl.startswith(LABEL_PREFIX + "ball_"):
        bid = lbl[len(LABEL_PREFIX):]
        ball_comps.append((bid, a.StaticMeshComponent))

print(f"[billiards] live balls: {[b[0] for b in ball_comps]}")

# Compute and apply shot velocity.
angle_deg = float(shot.get("angle_deg", 0.0))
speed = float(shot.get("speed_cm_s", 0.0))
speed = max(0.0, min(CUE_MAX_SPEED, speed))
rad = math.radians(angle_deg)
vx = math.cos(rad) * speed
vy = math.sin(rad) * speed
if cue_comp is not None:
    cue_comp.SetPhysicsLinearVelocity(_vec(vx, vy, 0.0), False, "None")
    print(f"[billiards] shot: angle={angle_deg:.1f} speed={speed:.0f} -> ({vx:.0f},{vy:.0f})")
else:
    print("[billiards] WARNING: no cue actor found — episode broken")


def _max_speed(bcs):
    vmax = 0.0
    for _, c in bcs:
        try:
            v = c.GetPhysicsLinearVelocity()
            s = (v.X*v.X + v.Y*v.Y + v.Z*v.Z) ** 0.5
            if s > vmax: vmax = s
        except Exception:
            pass
    return vmax


def _freeze(bcs):
    zero = _vec(0.0, 0.0, 0.0)
    for _, c in bcs:
        try:
            c.SetPhysicsLinearVelocity(zero, False, "None")
            if hasattr(c, "SetPhysicsAngularVelocityInDegrees"):
                c.SetPhysicsAngularVelocityInDegrees(zero, False)
        except Exception:
            pass


def _harvest(bcs):
    """Read positions; destroy any pocketed coloured ball; mark cue
    pocketed (but don't destroy — it may sit at -Z forever which is
    fine, we just record the flag). Write log_for_verify.txt."""
    cue_pocketed = False
    alive_target = 0
    pocketed_now = []
    cue_actor = None; cue_c = None
    for bid, c in bcs:
        try:
            actor = c.GetOwner()
            p = actor.K2_GetActorLocation()
            z = float(p.Z)
            if z < TABLE_TOP_Z - 10.0:
                if bid == "cue":
                    cue_pocketed = True   # respawn handled below
                    cue_actor = actor
                    cue_c = c
                else:
                    pocketed_now.append(bid)
                    actor.K2_DestroyActor()
            else:
                if bid == "cue":
                    cue_actor = actor
                    cue_c = c
                else:
                    alive_target += 1
        except Exception:
            pass

    # Cue scratched -> teleport back to table centre, zero its velocity.
    # Episode does NOT end; the agent just loses this turn's progress on
    # the cue, and fires from centre next round.
    if cue_pocketed and cue_actor is not None and cue_c is not None:
        try:
            tcx, tcy = params["table_center_xy"]
            cue_z = TABLE_TOP_Z + params["ball_radius"] + 1.0
            cue_actor.K2_SetActorLocation(_vec(tcx, tcy, cue_z), False)
            cue_c.SetPhysicsLinearVelocity(_vec(0.0, 0.0, 0.0), False, "None")
            if hasattr(cue_c, "SetPhysicsAngularVelocityInDegrees"):
                cue_c.SetPhysicsAngularVelocityInDegrees(_vec(0.0, 0.0, 0.0), False)
            print(f"[billiards] cue scratched -> respawn at table centre ({tcx:.0f},{tcy:.0f})")
        except Exception as e:
            print(f"[billiards] cue respawn failed: {e}")

    # Verdict is now purely based on alive coloured-ball count. Cue
    # scratch is reported as a flag but doesn't end the episode.
    if alive_target == 0:
        verdict = "ALL_DOWN"
    else:
        verdict = "ONGOING"

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"# alive_target={alive_target}\n")
        f.write(f"# pocketed_this_round={','.join(pocketed_now) if pocketed_now else '-'}\n")
        f.write(f"# cue_scratched={'yes' if cue_pocketed else 'no'}\n")
        f.write(f"# RESULT: {verdict}\n")
    print(f"[billiards] {verdict} alive={alive_target} new_pocketed={pocketed_now} cue_scratched={cue_pocketed}")

    # Also dump per-ball positions to state.json for harnesses that
    # feed the agent ground-truth structured state alongside the
    # screenshot (harness_image_state).  Pocketed coloured balls are
    # absent (their actors were destroyed); cue is at its post-
    # respawn position if it scratched this round.
    state = {
        "alive_target": alive_target,
        "cue_scratched": cue_pocketed,
        "pocketed_this_round": list(pocketed_now),
        "table_center_xy": list(params["table_center_xy"]),
        "table_size_x": params["table_size_x"],
        "table_size_y": params["table_size_y"],
        "table_top_z": TABLE_TOP_Z,
        "ball_radius": params["ball_radius"],
        "balls": {},
    }
    for bid, c in bcs:
        try:
            actor = c.GetOwner()
            p = actor.K2_GetActorLocation()
            state["balls"][bid] = [round(float(p.X), 1),
                                   round(float(p.Y), 1),
                                   round(float(p.Z), 1)]
        except Exception:
            # Actor was destroyed (pocketed coloured ball) — skip.
            pass
    state_path = os.path.join(BASE, "state.json")
    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[billiards] state.json write failed: {e}")


def _tick(delta_time, elapsed_time, actors, ctx):
    if ctx.get("stopped"):
        return False
    vmax = _max_speed(ctx["bcs"])

    # Tier 1: truly stopped (vmax < 8 cm/s held for 0.3 s) — fast path,
    # rare with damping=0.05 but happens for short shots.
    settled = False
    settle_reason = None
    if elapsed_time >= SETTLE_MIN_T and vmax < SETTLE_VEL_CM_S:
        ss = ctx.get("settle_since")
        if ss is None:
            ctx["settle_since"] = elapsed_time
        elif elapsed_time - ss >= SETTLE_HOLD_S:
            settled = True
            settle_reason = f"settled vmax={vmax:.1f}cm/s"
    else:
        ctx["settle_since"] = None

    # Past the soft cap: only freeze once balls have slowed enough that
    # snapping them to rest doesn't visibly teleport them. If the cue is
    # still flying at >50 cm/s, keep waiting — otherwise the screenshot
    # would show a ball at a position the next shot doesn't act on.
    if not settled and elapsed_time >= SOFT_CAP_S:
        if vmax < HARD_CAP_FREEZE_VEL_CM_S:
            settled = True
            settle_reason = f"slow_freeze vmax={vmax:.1f}cm/s past soft cap"
        elif elapsed_time >= EXTENDED_CAP_S:
            settled = True   # absolute ceiling — accept the visual jump
            settle_reason = f"extended_cap_force vmax={vmax:.1f}cm/s"

    if not settled:
        return True

    print(f"[billiards] stop t={elapsed_time:.1f}s reason={settle_reason}")
    _freeze(ctx["bcs"])
    _harvest(ctx["bcs"])
    if ctx["record"]:
        recording = ur.StopRecording()
        with open(os.path.join(BASE, "recording_current.json"), "w", encoding="utf-8") as recording_file:
            json.dump(recording, recording_file)
    ctx["stopped"] = True
    return False


if RECORD:
    ur.StartRecording(width=640, height=480, codec="h264")
    print("[billiards] StartRecording 640x480 h264")

ur.submit_tick_task(
    "billiards_round", _tick, [],
    {"bcs": ball_comps, "record": RECORD,
     "stopped": False, "settle_since": None},
    max_duration=RECORD_DURATION_S + 4.0,
)
print("[billiards] tick task submitted")
'''


# ---------------------------------------------------------------------------
# UE-side scene code: SIMULATE a hypothetical shot (snapshot → kick → tick →
# record events → ROLLBACK to original positions).  Used by the
# ``harness_querytool`` REPL where the agent gets a ``simulate_shot`` tool
# that returns the deterministic outcome of a candidate shot WITHOUT
# advancing real game state.  Any ball "pocketed" during a simulate is
# restored — actors are NEVER destroyed and ``state.json`` /
# ``log_for_verify.txt`` are NEVER touched.
# ---------------------------------------------------------------------------
SIMULATE_TICK_CODE = r'''import unreal_runtime as ur
import json
import math
import os

BASE = r"__BASE__"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

shot = json.loads(r"""__SHOT_JSON__""")
SIM_OUT_PATH = r"__SIM_OUT__"     # absolute path the harness will poll

LABEL_PREFIX = "__LABEL__"
TABLE_TOP_Z = params["table_top_z"]
BALL_RADIUS = params["ball_radius"]
CUE_MAX_SPEED = params["cue_max_speed_cm_s"]

# Same settle thresholds as SHOT_TICK_CODE so simulate ↔ real shot are
# physically identical (the only difference is rollback at the end).
SETTLE_VEL_CM_S = 8.0
SETTLE_HOLD_S = 0.3
SETTLE_MIN_T = 0.8
SOFT_CAP_S = 10.0
HARD_CAP_FREEZE_VEL_CM_S = 50.0
EXTENDED_CAP_S = 25.0

CONTACT_DIST = (BALL_RADIUS * 2.0) + 2.0   # ball centres within 2*r + slack


def _vec(x, y, z):
    v = ur.CoreUObject.Vector(); v.X = x; v.Y = y; v.Z = z; return v


# Look up cue + all live balls by label.
world = ur.Engine.GetDefaultWorld()
cue_comp = None
ball_comps = []  # (id, comp)
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    lbl = a.GetActorLabel()
    if not lbl.startswith(LABEL_PREFIX): continue
    if lbl == LABEL_PREFIX + "cue":
        cue_comp = a.StaticMeshComponent
        ball_comps.append(("cue", cue_comp))
    elif lbl.startswith(LABEL_PREFIX + "ball_"):
        bid = lbl[len(LABEL_PREFIX):]
        ball_comps.append((bid, a.StaticMeshComponent))

# Snapshot positions BEFORE applying velocity so we can restore.
snapshot = {}    # bid → (x, y, z)
for bid, c in ball_comps:
    try:
        p = c.GetOwner().K2_GetActorLocation()
        snapshot[bid] = (float(p.X), float(p.Y), float(p.Z))
    except Exception:
        pass

# Apply candidate cue velocity.
angle_deg = float(shot.get("angle_deg", 0.0))
speed = float(shot.get("speed_cm_s", 0.0))
speed = max(0.0, min(CUE_MAX_SPEED, speed))
rad = math.radians(angle_deg)
vx = math.cos(rad) * speed
vy = math.sin(rad) * speed
if cue_comp is not None:
    cue_comp.SetPhysicsLinearVelocity(_vec(vx, vy, 0.0), False, "None")
    print(f"[billiards/sim] kick angle={angle_deg:.1f} speed={speed:.0f}")
else:
    print("[billiards/sim] WARNING: no cue actor")


def _max_speed(bcs):
    vmax = 0.0
    for _, c in bcs:
        try:
            v = c.GetPhysicsLinearVelocity()
            s = (v.X*v.X + v.Y*v.Y + v.Z*v.Z) ** 0.5
            if s > vmax: vmax = s
        except Exception: pass
    return vmax


def _freeze(bcs):
    zero = _vec(0.0, 0.0, 0.0)
    for _, c in bcs:
        try:
            c.SetPhysicsLinearVelocity(zero, False, "None")
            if hasattr(c, "SetPhysicsAngularVelocityInDegrees"):
                c.SetPhysicsAngularVelocityInDegrees(zero, False)
        except Exception: pass


def _restore(bcs, snap):
    """Teleport every ball back to its pre-simulate position; zero velocity."""
    zero = _vec(0.0, 0.0, 0.0)
    for bid, c in bcs:
        if bid not in snap: continue
        x, y, z = snap[bid]
        try:
            actor = c.GetOwner()
            actor.K2_SetActorLocation(_vec(x, y, z), False)
            c.SetPhysicsLinearVelocity(zero, False, "None")
            if hasattr(c, "SetPhysicsAngularVelocityInDegrees"):
                c.SetPhysicsAngularVelocityInDegrees(zero, False)
        except Exception as e:
            print(f"[billiards/sim] restore {bid} failed: {e}")


def _tick(delta_time, elapsed_time, actors, ctx):
    if ctx.get("stopped"):
        return False
    bcs = ctx["bcs"]

    # --- Per-tick event detection ---
    # 1. Pocketed balls (z dropped below table)
    for bid, c in bcs:
        if bid in ctx["pocketed"] or bid == "cue" and ctx["cue_pocketed"]:
            continue
        try:
            p = c.GetOwner().K2_GetActorLocation()
            if float(p.Z) < TABLE_TOP_Z - 10.0:
                if bid == "cue":
                    ctx["cue_pocketed"] = True
                    ctx["events"].append(
                        f"t={elapsed_time:.2f} cue_pocketed")
                else:
                    ctx["pocketed"].add(bid)
                    ctx["events"].append(
                        f"t={elapsed_time:.2f} {bid}_pocketed")
        except Exception: pass

    # 2. First cue contact (only the FIRST one — useful for "did I hit
    #    what I aimed at" debugging without flooding the event stream).
    if ctx["first_cue_contact"] is None:
        try:
            cue_a = None
            for bid, c in bcs:
                if bid == "cue":
                    cue_a = c.GetOwner(); break
            if cue_a is not None:
                cue_p = cue_a.K2_GetActorLocation()
                cx, cy = float(cue_p.X), float(cue_p.Y)
                for bid, c in bcs:
                    if bid == "cue": continue
                    if bid in ctx["pocketed"]: continue
                    try:
                        p = c.GetOwner().K2_GetActorLocation()
                        dx = float(p.X) - cx; dy = float(p.Y) - cy
                        d = (dx*dx + dy*dy) ** 0.5
                        if d < CONTACT_DIST:
                            ctx["first_cue_contact"] = bid
                            ctx["events"].append(
                                f"t={elapsed_time:.2f} cue→{bid}")
                            break
                    except Exception: pass
        except Exception: pass

    # --- Settle detection (same logic as SHOT_TICK_CODE) ---
    vmax = _max_speed(bcs)
    settled = False
    settle_reason = None
    if elapsed_time >= SETTLE_MIN_T and vmax < SETTLE_VEL_CM_S:
        ss = ctx.get("settle_since")
        if ss is None:
            ctx["settle_since"] = elapsed_time
        elif elapsed_time - ss >= SETTLE_HOLD_S:
            settled = True
            settle_reason = f"settled vmax={vmax:.1f}cm/s"
    else:
        ctx["settle_since"] = None
    if not settled and elapsed_time >= SOFT_CAP_S:
        if vmax < HARD_CAP_FREEZE_VEL_CM_S:
            settled = True
            settle_reason = f"slow_freeze vmax={vmax:.1f}cm/s past soft cap"
        elif elapsed_time >= EXTENDED_CAP_S:
            settled = True
            settle_reason = f"extended_cap_force vmax={vmax:.1f}cm/s"
    if not settled:
        return True

    # --- Settled: capture FINAL positions, then ROLLBACK ---
    _freeze(bcs)
    final_pos = {}
    for bid, c in bcs:
        try:
            p = c.GetOwner().K2_GetActorLocation()
            final_pos[bid] = [round(float(p.X), 1),
                              round(float(p.Y), 1),
                              round(float(p.Z), 1)]
        except Exception: pass

    result = {
        "ok": True,
        "shot": {"angle_deg": ctx["angle_deg"], "speed_cm_s": ctx["speed"]},
        "duration_s": round(elapsed_time, 2),
        "settle_reason": settle_reason,
        "pocketed_balls": sorted(ctx["pocketed"]),
        "cue_pocketed": ctx["cue_pocketed"],
        "first_cue_contact": ctx["first_cue_contact"],
        "ball_final_pos": final_pos,
        "events": list(ctx["events"]),
    }

    # ROLLBACK before writing the result file (so even if the result
    # write fails, scene state is already restored).
    _restore(bcs, ctx["snapshot"])

    try:
        with open(SIM_OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"[billiards/sim] DONE pocketed={result['pocketed_balls']} "
              f"cue_pocketed={result['cue_pocketed']} "
              f"first_contact={result['first_cue_contact']} "
              f"-> {SIM_OUT_PATH}")
    except Exception as e:
        print(f"[billiards/sim] result write failed: {e}")

    ctx["stopped"] = True
    return False


ur.submit_tick_task(
    "billiards_simulate", _tick, [],
    {"bcs": ball_comps, "snapshot": snapshot,
     "angle_deg": angle_deg, "speed": speed,
     "stopped": False, "settle_since": None,
     "pocketed": set(), "cue_pocketed": False,
     "first_cue_contact": None, "events": []},
    max_duration=EXTENDED_CAP_S + 4.0,
)
print("[billiards/sim] tick task submitted")
'''


# ---------------------------------------------------------------------------
# UE-side scene code: SPAWN / CLEAR an aim-line preview.
# A thin coloured beam emanating from the cue ball at the agent's chosen
# angle, with no collision, used by the aim-line preview harness so the
# agent can visually confirm intended direction before committing.
# ---------------------------------------------------------------------------
AIM_LINE_CODE = r'''import unreal_runtime as ur
import json, math, os

BASE = r"__BASE__"
ANGLE_DEG = float(__ANGLE__)
LENGTH_CM = float(__LENGTH__)
LABEL_PREFIX = "__LABEL__"
LINE_LABEL = LABEL_PREFIX + "aim_line"

with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

TABLE_TOP_Z = params["table_top_z"]
BALL_RADIUS = params["ball_radius"]


def _vec(x, y, z):
    v = ur.CoreUObject.Vector(); v.X = x; v.Y = y; v.Z = z; return v


# Find cue ball position
world = ur.Engine.GetDefaultWorld()
cue_pos = None
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        if a.GetActorLabel() == LABEL_PREFIX + "cue":
            p = a.K2_GetActorLocation()
            cue_pos = (float(p.X), float(p.Y), float(p.Z))
            break
    except Exception:
        pass

if cue_pos is None:
    print("[aim_line] ERROR: no cue actor found")
else:
    # Destroy any pre-existing aim line so we don't accumulate.
    for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
        try:
            if a.GetActorLabel() == LINE_LABEL:
                a.K2_DestroyActor()
        except Exception:
            pass

    # Spawn the new aim line.  Box mesh, very thin, length-along-X
    # before rotation; yaw=ANGLE rotates the local +X to (cos, sin).
    THICK_CM = 8.0    # ~8 cm wide → 1-2 px at 320×240 zoom
    HEIGHT_CM = 2.0   # very flat — visible from above, not from side
    rad = math.radians(ANGLE_DEG)
    cx, cy, cz = cue_pos
    # Centre at cue + half-length along the angle direction so the line
    # starts at the cue and extends outward in (cos, sin).
    center_x = cx + 0.5 * LENGTH_CM * math.cos(rad)
    center_y = cy + 0.5 * LENGTH_CM * math.sin(rad)
    # Sit just above the ball top so it's visible from the camera but
    # never intersects ball collisions (which we also disable below).
    center_z = TABLE_TOP_Z + BALL_RADIUS + 1.0

    cube = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
        "/Engine/BasicShapes/Cube.Cube")
    base_mat = ur.StaticLoadObject(
        ur.Engine.MaterialInterface, None,
        "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")

    t = ur.CoreUObject.Transform()
    t.Translation = _vec(center_x, center_y, center_z)

    rot = ur.CoreUObject.Rotator()
    rot.Pitch = 0.0; rot.Yaw = ANGLE_DEG; rot.Roll = 0.0
    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(rot)

    sv = ur.CoreUObject.Vector()
    # Cube default is 100×100×100; Scale = size_cm / 100.
    sv.X = LENGTH_CM / 100.0
    sv.Y = THICK_CM / 100.0
    sv.Z = HEIGHT_CM / 100.0
    t.Scale3D = sv

    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    c = a.StaticMeshComponent
    c.SetMobility(2)
    c.SetStaticMesh(cube)
    if base_mat:
        c.SetMaterial(0, base_mat)
        mid = c.CreateDynamicMaterialInstance(0)
        col = ur.CoreUObject.LinearColor()
        col.R, col.G, col.B, col.A = 1.0, 0.05, 0.05, 1.0  # bright red
        mid.SetVectorParameterValue("Color", col)
    c.SetCollisionEnabled(0)   # never interact with balls
    a.SetActorLabel(LINE_LABEL)
    print(f"[aim_line] spawned angle={ANGLE_DEG:.1f} length={LENGTH_CM:.0f} "
          f"from cue=({cx:.0f},{cy:.0f}) -> centre=({center_x:.0f},{center_y:.0f})")
'''


AIM_LINE_CLEAR_CODE = r'''import unreal_runtime as ur

LABEL_PREFIX = "__LABEL__"
LINE_LABEL = LABEL_PREFIX + "aim_line"

world = ur.Engine.GetDefaultWorld()
n = 0
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        if a.GetActorLabel() == LINE_LABEL:
            a.K2_DestroyActor()
            n += 1
    except Exception:
        pass
print(f"[aim_line] cleared {n} actor(s)")
'''


def assemble_setup(workspace: Path) -> str:
    base = str(workspace).replace("\\", "/")
    return (
        SCENE_SETUP_CODE
        .replace("__BASE__", base)
        .replace("__LABEL__", LABEL_PREFIX)
    )


def assemble_simulate(shot: Dict[str, Any], workspace: Path,
                      sim_out: Path) -> str:
    """Build the UE-side script for a hypothetical-shot simulate that
    rolls back scene state when done. ``sim_out`` is the absolute path
    where the result JSON will be written (host polls this)."""
    base = str(workspace).replace("\\", "/")
    out = str(sim_out).replace("\\", "/")
    return (
        SIMULATE_TICK_CODE
        .replace("__BASE__", base)
        .replace("__SHOT_JSON__", json.dumps(shot))
        .replace("__SIM_OUT__", out)
        .replace("__LABEL__", LABEL_PREFIX)
    )


def assemble_aim_line(workspace: Path, angle_deg: float,
                      length_cm: float = 1500.0) -> str:
    """Build UE-side script that spawns a thin red beam from the cue ball
    along ``angle_deg`` (same convention as a real shot: 0=+X, 90=+Y).
    Default length 1500cm covers the diagonal of the hard preset
    (~1400cm) so the line always reaches a rail regardless of angle.
    Replaces any existing aim-line actor. No collision."""
    base = str(workspace).replace("\\", "/")
    return (
        AIM_LINE_CODE
        .replace("__BASE__", base)
        .replace("__ANGLE__", str(float(angle_deg)))
        .replace("__LENGTH__", str(float(length_cm)))
        .replace("__LABEL__", LABEL_PREFIX)
    )


def assemble_clear_aim_line() -> str:
    """Build UE-side script that destroys any aim-line actor."""
    return AIM_LINE_CLEAR_CODE.replace("__LABEL__", LABEL_PREFIX)


def assemble_shot(shot: Dict[str, Any], workspace: Path, modality: str) -> str:
    """``modality`` is ``"video"`` or ``"image"``. Video mode wraps
    the round in StartRecording / StopRecording. Image mode skips
    the recording entirely (host snaps a screenshot after settle)."""
    base = str(workspace).replace("\\", "/")
    return (
        SHOT_TICK_CODE
        .replace("__BASE__", base)
        .replace("__SHOT_JSON__", json.dumps(shot))
        .replace("__MODALITY__", modality)
        .replace("__LABEL__", LABEL_PREFIX)
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
_CODE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
_THOUGHT_RE = re.compile(r"^(.*?)```", re.DOTALL)


def extract_thought(response: str) -> str:
    m = _THOUGHT_RE.match(response)
    return m.group(1).strip() if m else response.strip()


def extract_shot(response: str) -> Optional[Dict[str, float]]:
    m = _CODE_RE.search(response)
    if not m: return None
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return None
    if not isinstance(obj, dict): return None
    try:
        return {"angle_deg": float(obj.get("angle_deg", 0.0)),
                "speed_cm_s": float(obj.get("speed_cm_s", 0.0))}
    except (TypeError, ValueError):
        return None


def parse_verify_log(log_text: str) -> Tuple[Optional[str], int, bool]:
    """Returns (verdict, alive_target_count, cue_scratched_this_round).
    verdict is one of ``"SETUP"`` (round-0 only), ``"ONGOING"``,
    ``"ALL_DOWN"`` (PASS — all coloured pocketed). Cue scratching no
    longer ends the episode — the cue is teleported back to the table
    centre and the agent fires from there next round; the
    ``cue_scratched`` flag is reported for scoreboard purposes only."""
    verdict: Optional[str] = None
    # A missing count is an infrastructure failure, not zero remaining balls.
    # Pre-difficulty code defaulted to 5 (the standard preset's target
    # count) but that lies in hard mode (10 target balls).
    alive = None
    cue_scratched = False
    for line in log_text.splitlines():
        if line.startswith("# RESULT:"):
            verdict = line.split(":", 1)[1].strip()
        m = re.search(r"alive_target=(\d+)", line)
        if m: alive = int(m.group(1))
        # New format uses cue_scratched=; tolerate the legacy
        # cue_pocketed= name for any old logs left on disk.
        m = re.search(r"cue_(?:scratched|pocketed)=(yes|no)", line)
        if m: cue_scratched = (m.group(1) == "yes")
    if verdict not in ("SETUP", "ONGOING", "ALL_DOWN") or alive is None:
        raise RuntimeError("Billiards verifier output is incomplete")
    return verdict, alive, cue_scratched


def extract_repl_action(response: str) -> Optional[Dict[str, Any]]:
    """Parse a harness_querytool agent turn into one of:

    * ``{"action": "query", "tool": "simulate_shot",
         "args": {"angle_deg": .., "speed_cm_s": ..}}``
    * ``{"action": "shoot", "shot": {"angle_deg": .., "speed_cm_s": ..}}``

    Returns a normalised dict or ``None`` if neither shape parses.
    Tolerates the old ``{"angle_deg":.., "speed_cm_s":..}`` bare-shot
    shape too (treats it as ``shoot``).
    """
    m = _CODE_RE.search(response)
    if not m: return None
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return None
    if not isinstance(obj, dict): return None

    # Bare shot shape (sibling-compatible fallback).
    if {"angle_deg", "speed_cm_s"} <= set(obj.keys()):
        try:
            return {"action": "shoot",
                    "shot": {"angle_deg": float(obj["angle_deg"]),
                             "speed_cm_s": float(obj["speed_cm_s"])}}
        except (TypeError, ValueError):
            return None

    action = obj.get("action")
    if action == "shoot":
        s = obj.get("shot") or {}
        try:
            return {"action": "shoot",
                    "shot": {"angle_deg": float(s.get("angle_deg", 0.0)),
                             "speed_cm_s": float(s.get("speed_cm_s", 0.0))}}
        except (TypeError, ValueError):
            return None
    if action == "query":
        tool = obj.get("tool")
        args = obj.get("args") or {}
        if tool != "simulate_shot": return None
        try:
            return {"action": "query", "tool": "simulate_shot",
                    "args": {"angle_deg": float(args.get("angle_deg", 0.0)),
                             "speed_cm_s": float(args.get("speed_cm_s", 0.0))}}
        except (TypeError, ValueError):
            return None
    return None


def extract_aimline_decision(response: str) -> Optional[Dict[str, Any]]:
    """Parse a harness_aimline_confirm follow-up turn into one of:

    * ``{"action": "confirm"}`` — fire the proposed shot as-is
    * ``{"action": "adjust",
         "shot": {"angle_deg": .., "speed_cm_s": ..}}`` — re-aim and ask again

    Accepts either ``{"confirm": true}`` or ``{"action": "confirm"}``;
    either ``{"adjust": {...}}`` or ``{"action": "adjust", "shot": {...}}``.
    Returns ``None`` if the JSON block is missing / malformed.
    """
    m = _CODE_RE.search(response)
    if not m: return None
    try:
        obj = json.loads(m.group(1).strip())
    except Exception:
        return None
    if not isinstance(obj, dict): return None

    action = obj.get("action")
    # confirm shapes
    if obj.get("confirm") is True or action == "confirm":
        return {"action": "confirm"}
    # adjust shapes
    if "adjust" in obj and isinstance(obj["adjust"], dict):
        s = obj["adjust"]
        try:
            return {"action": "adjust",
                    "shot": {"angle_deg": float(s.get("angle_deg", 0.0)),
                             "speed_cm_s": float(s.get("speed_cm_s", 0.0))}}
        except (TypeError, ValueError):
            return None
    if action == "adjust":
        s = obj.get("shot") or {}
        try:
            return {"action": "adjust",
                    "shot": {"angle_deg": float(s.get("angle_deg", 0.0)),
                             "speed_cm_s": float(s.get("speed_cm_s", 0.0))}}
        except (TypeError, ValueError):
            return None
    return None


__all__ = [
    "HERE", "LABEL_PREFIX",
    "SCENE_SETUP_CODE", "SHOT_TICK_CODE", "SIMULATE_TICK_CODE",
    "AIM_LINE_CODE", "AIM_LINE_CLEAR_CODE",
    "assemble_setup", "assemble_shot", "assemble_simulate",
    "assemble_aim_line", "assemble_clear_aim_line",
    "extract_thought", "extract_shot", "extract_repl_action",
    "extract_aimline_decision",
    "parse_verify_log",
]
