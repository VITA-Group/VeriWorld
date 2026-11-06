"""Shared UE code + helpers for moving_shooter harnesses.

Translates VeriWorld's setup.py / clean.py / tick_runner.py into
``python_exec``-ready code strings, and adds harness-side utilities:
parsing the agent's response, harvesting the recorded video, reading
``round_log.txt`` produced by the tick runner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Per-round ceiling on the model call, shared by every harness arm so the
# arms cannot drift apart (a longer allowance in one arm would hand that
# condition a real advantage).
#
# 180 s was too tight to be a safety net: it is *below* the honest latency
# of the slowest panel members on this prompt. Measured 2026-07-25 on the
# seed-0 equations — gpt-5-mini 42 s, gemini-3.1-pro 97 s,
# claude-sonnet-4-6 152 s, qwen3-vl-30b 265 s (it needs ~22k output tokens
# to finish a reply). At 180 s the two slowest models would have been
# recorded as timeouts on nearly every round, which reads in the results
# as "cannot do the task" rather than "the harness hung up on it".
VLM_TIMEOUT_S = float(os.environ.get("RS_VLM_TIMEOUT_S", "420"))


# ---------------------------------------------------------------------------
# Level switch
# ---------------------------------------------------------------------------
LEVEL_SWITCH_CODE = (
    "import unreal_runtime as ur\n"
    'cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
    'if cur != "Untitled":\n'
    '    ur.Engine.GameplayStatics.OpenLevel("Untitled", True, "")\n'
    '    print("LEVEL_SWITCHING")\n'
    'else:\n'
    '    print("LEVEL_READY")\n'
)


# ---------------------------------------------------------------------------
# Clean — destroy any RS_* actors from a previous round.
# ---------------------------------------------------------------------------
CLEAN_CODE = (
    "import unreal_runtime as ur\n"
    "world = ur.Engine.GetDefaultWorld()\n"
    "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
    "    lbl = a.GetActorLabel()\n"
    '    if lbl.startswith("RS_"):\n'
    "        a.K2_DestroyActor()\n"
    "import builtins\n"
    "builtins._RS_RESULT = ''\n"
    "builtins._RS_VIDEO_PATH = ''\n"
    "builtins._RS_VIDEO_FILENAME = ''\n"
    "builtins._RS_TICKS = 0\n"
    'print("RS_CLEAN_DONE")\n'
)


# ---------------------------------------------------------------------------
# Setup scene — pillar / target / arm / materials / camera
# ---------------------------------------------------------------------------
def setup_scene_code(params: dict) -> str:
    p_json = json.dumps(params)
    # NOTE: on-screen debug messages (VoxelWorld task counters etc.)
    # are suppressed via the packaged build's Saved config
    # (Engine.ini: bEnableOnScreenDebugMessages=False), NOT from here —
    # calling KismetSystemLibrary.ExecuteConsoleCommand through the
    # reflection bridge hard-crashes the cooked build (static UFunction
    # + WorldContext meta-param path; see crash dumps 2026-07-25).
    return (
        "import unreal_runtime as ur\n"
        "import json, math\n"
        f"P = json.loads(r'''{p_json}''')\n"
        "PILLAR_HEIGHT = P.get('pillar_height', 300.0)\n"
        "PILLAR_SCALE = (0.5, 0.5, 3.0)\n"
        "PILLAR_Z = PILLAR_HEIGHT * 0.5\n"
        "TARGET_Z = P.get('target_z', 325.0)\n"
        "TARGET_SCALE = 0.5\n"
        "TARGET_ORBIT_R = P.get('target_orbit_r', 180.0)\n"
        "TARGET_PHASE = P.get('target_phase', 0.0)\n"
        "ARM_ORBIT_R = P.get('arm_orbit_r', 650.0)\n"
        "ARM_PHASE = P.get('arm_phase', 0.0)\n"
        "ARM_SCALE = (2.0, 0.2, 0.2)\n"
        "ARM_LENGTH = ARM_SCALE[0] * 100.0\n"
        # Initial (t=0) target position in world frame.
        "TARGET_START_X = TARGET_ORBIT_R * math.cos(TARGET_PHASE)\n"
        "TARGET_START_Y = TARGET_ORBIT_R * math.sin(TARGET_PHASE)\n"
        "ARM_BACK_X = ARM_ORBIT_R * math.cos(ARM_PHASE)\n"
        "ARM_BACK_Y = ARM_ORBIT_R * math.sin(ARM_PHASE)\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        # Destroy any leftover RS_* actors.
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    lbl = a.GetActorLabel()\n"
        "    if lbl.startswith('RS_'):\n"
        "        a.K2_DestroyActor()\n"
        "def _make_xform(pos, rot=(0, 0, 0), scale=(1, 1, 1)):\n"
        "    t = ur.CoreUObject.Transform()\n"
        "    t.Translation = ur.CoreUObject.Vector(*pos)\n"
        "    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(\n"
        "        ur.CoreUObject.Rotator(*rot))\n"
        "    sv = ur.CoreUObject.Vector(); sv.X, sv.Y, sv.Z = scale\n"
        "    t.Scale3D = sv\n"
        "    return t\n"
        "def _spawn_static(pos, mesh_path, scale, label):\n"
        "    s = scale if isinstance(scale, tuple) else (scale, scale, scale)\n"
        "    actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, _make_xform(pos, scale=s), 1)\n"
        "    if not actor: return None, None\n"
        "    mc = actor.StaticMeshComponent\n"
        "    mc.SetMobility(2)\n"
        "    mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(mesh_path)\n"
        "    if mesh: mc.SetStaticMesh(mesh)\n"
        "    actor.SetActorLabel(label)\n"
        "    return actor, mc\n"
        # Pillar (gray cube), target (blue sphere), arm (green elongated cube).
        "pillar_actor, pillar_comp = _spawn_static(\n"
        "    (TARGET_START_X, TARGET_START_Y, PILLAR_Z),\n"
        "    '/Engine/BasicShapes/Cube.Cube', PILLAR_SCALE, 'RS_Pillar')\n"
        "if pillar_comp:\n"
        "    pillar_comp.SetCollisionEnabled(1)\n"
        "    pillar_comp.SetCollisionProfileName('BlockAll')\n"
        "target_actor, target_comp = _spawn_static(\n"
        "    (TARGET_START_X, TARGET_START_Y, TARGET_Z),\n"
        "    '/Engine/BasicShapes/Sphere.Sphere', TARGET_SCALE, 'RS_Target')\n"
        "if target_comp:\n"
        "    target_comp.SetCollisionEnabled(1)\n"
        "    target_comp.SetCollisionProfileName('BlockAll')\n"
        "arm_yaw = math.degrees(math.atan2(\n"
        "    TARGET_START_Y - ARM_BACK_Y, TARGET_START_X - ARM_BACK_X))\n"
        "arm_cx = ARM_BACK_X + math.cos(math.radians(arm_yaw)) * (ARM_LENGTH * 0.5)\n"
        "arm_cy = ARM_BACK_Y + math.sin(math.radians(arm_yaw)) * (ARM_LENGTH * 0.5)\n"
        "arm_actor, arm_comp = _spawn_static(\n"
        "    (arm_cx, arm_cy, TARGET_Z),\n"
        "    '/Engine/BasicShapes/Cube.Cube', ARM_SCALE, 'RS_Arm')\n"
        "if arm_comp:\n"
        "    arm_rot = ur.CoreUObject.Rotator()\n"
        "    arm_rot.Pitch = 0.0; arm_rot.Yaw = arm_yaw; arm_rot.Roll = 0.0\n"
        "    arm_actor.K2_SetActorRotation(arm_rot, False)\n"
        "    arm_comp.SetCollisionEnabled(0)\n"
        # Materials — cooked-build-safe recipe, mirrored from
        # billiards _apply_tint (appendix F.3): load BasicShapeMaterial,
        # set it on slot 0, then drive its 'Color' parameter through a
        # dynamic material instance. Runtime HLSL compilation
        # (CreateMaterialFromCodeAndWait) crashes cooked builds
        # ("uncooked shader map ID in a cooked application"), which is
        # why the VeriWorld-era code cannot run against the packaged
        # binary. Colors keep the original scheme: gray pillar, blue
        # target, green arm, red projectile.
        "_base_mat = ur.StaticLoadObject(\n"
        "    ur.Engine.MaterialInterface, None,\n"
        "    '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial')\n"
        "def _tint(comp, r, g, b):\n"
        "    if not comp or not _base_mat: return\n"
        "    comp.SetMaterial(0, _base_mat)\n"
        "    mid = comp.CreateDynamicMaterialInstance(0)\n"
        "    col = ur.CoreUObject.LinearColor()\n"
        "    col.R, col.G, col.B, col.A = r, g, b, 1.0\n"
        "    if mid: mid.SetVectorParameterValue('Color', col)\n"
        "_tint(pillar_comp, 0.5, 0.5, 0.55)\n"
        "_tint(target_comp, 0.1, 0.3, 0.9)\n"
        "_tint(arm_comp, 0.1, 0.8, 0.2)\n"
        # Camera.
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if pawns and len(pawns) > 0:\n"
        "    pawn = pawns[0]; controller = pawn.GetController()\n"
        "    pawn.K2_SetActorLocation(ur.CoreUObject.Vector(1200, -1200, 900), False)\n"
        "    cr = ur.CoreUObject.Rotator(); cr.Pitch=-25.0; cr.Yaw=135.0; cr.Roll=0.0\n"
        "    controller.SetControlRotation(cr)\n"
        "print('RS_SETUP_DONE')\n"
    )


# ---------------------------------------------------------------------------
# Tick runner — animates target / arm, fires projectile, writes
# round_log.txt to ``log_dir`` (workspace path), sets builtins for
# polling.
# ---------------------------------------------------------------------------
def tick_runner_code(
    params: dict,
    fire_time: float,
    aim_yaw: float,
    aim_pitch: float,
    run_id: int,
    log_dir: str,
    *,
    record: bool = True,
) -> str:
    p_json = json.dumps(params)
    # Forward-slash the log path so it's safe to embed in the code
    # string regardless of OS.
    log_dir_fwd = str(log_dir).replace("\\", "/")
    return (
        "import unreal_runtime as ur\n"
        "import json, math, os, builtins, time\n"
        f"P = json.loads(r'''{p_json}''')\n"
        f"FIRE_TIME = {float(fire_time)}\n"
        f"AIM_YAW = {float(aim_yaw)}\n"
        f"AIM_PITCH = {float(aim_pitch)}\n"
        f"RUN_ID = {int(run_id)}\n"
        f"LOG_DIR = r'''{log_dir_fwd}'''\n"
        f"RECORD = {bool(record)}\n"
        # Reset builtins so harness-side polling can detect this round.
        "builtins._RS_RESULT = 'NONE'\n"
        "builtins._RS_VIDEO_PATH = ''\n"
        "builtins._RS_VIDEO_FILENAME = ''\n"
        "builtins._RS_TICKS = 0\n"
        # Pull motion params.
        "PILLAR_HEIGHT = P.get('pillar_height', 300.0)\n"
        "TARGET_Z_BASE = P.get('target_z_base', P.get('target_z', 325.0))\n"
        "PILLAR_Z = PILLAR_HEIGHT * 0.5\n"
        "TARGET_ORBIT_R = P.get('target_orbit_r', 180.0)\n"
        "TARGET_OMEGA   = P.get('target_omega', 0.6283)\n"
        "TARGET_PHASE   = P.get('target_phase', 0.0)\n"
        "TARGET_Z_AMP   = P.get('target_z_amp', 0.0)\n"
        "TARGET_Z_FREQ  = P.get('target_z_freq', 2.0)\n"
        "ARM_ANGULAR_SPEED = P.get('arm_angular_speed', 80.0)\n"
        "ARM_ORBIT_R       = P.get('arm_orbit_r', 650.0)\n"
        "ARM_R_AMP         = P.get('arm_r_amp', 120.0)\n"
        "ARM_R_FREQ        = P.get('arm_r_freq', 3.0)\n"
        "ARM_Z_AMP         = P.get('arm_z_amp', 60.0)\n"
        "ARM_Z_FREQ        = P.get('arm_z_freq', 2.0)\n"
        "ARM_PHASE         = P.get('arm_phase', 0.0)\n"
        "ARM_LENGTH        = P.get('arm_length', 200.0)\n"
        "PROJ_V0           = P.get('proj_v0', 2000.0)\n"
        "PROJ_DECAY_K      = P.get('proj_decay_k', 5.0)\n"
        "PROJ_GRAVITY      = P.get('proj_gravity', 400.0)\n"
        "PROJ_STALL_SPEED  = P.get('proj_stall_speed', 50.0)\n"
        "HIT_RADIUS        = P.get('hit_radius', 45.0)\n"
        "FRAME_ROT         = P.get('frame_rotation_rad', 0.0)\n"
        "MAX_SIM_TIME      = P.get('max_sim_time', 20.0)\n"
        "OBSERVE_TIME      = P.get('observe_time', 10.0)\n"
        "IMPULSE_MAG = 50000.0\n"
        "PROJ_SCALE = 0.15\n"
        # Find actors (must already be spawned by setup).
        "world = ur.Engine.GetDefaultWorld()\n"
        "arm_actor = target_actor = pillar_actor = None\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    lbl = a.GetActorLabel()\n"
        "    if lbl == 'RS_Arm': arm_actor = a\n"
        "    elif lbl == 'RS_Target': target_actor = a\n"
        "    elif lbl == 'RS_Pillar': pillar_actor = a\n"
        "if not arm_actor or not target_actor or not pillar_actor:\n"
        "    builtins._RS_RESULT = 'ERROR'\n"
        "    print('RS_TICK_ERROR no actors')\n"
        "else:\n"
        # Recording start is retried: right after an OpenLevel switch the
        # recorder subsystem is not always ready, and a single silent
        # failure here cost 55% of round-1 frame grids in the 2026-07-25
        # vision sweep (the observe round is always the first recording
        # after a level switch). The sim itself is unaffected either way.
        "    if RECORD:\n"
        # Clear any stale recorder first. A tick task that dies before its
        # StopRecording line (poll timeout, actors destroyed under it)
        # leaves the engine-wide recorder active forever, and every later
        # StartRecording on that engine fails "already recording" — the
        # actual exception behind 55%/32% of round-1 grids going missing
        # on 2026-07-25/26. Retrying start without stopping cannot help.
        "        try:\n"
        "            _stale = ur.StopRecording()\n"
        "            print(f'RS_REC_CLEARED_STALE {_stale.get(\"filename\", \"\") if isinstance(_stale, dict) else _stale}')\n"
        "        except Exception:\n"
        "            pass\n"
        "        for _rec_try in range(3):\n"
        "            try:\n"
        "                rec_path = ur.StartRecording(width=640, height=480, codec='h264')\n"
        "                print(f'RS_REC_START {rec_path}')\n"
        "                break\n"
        "            except Exception as _e:\n"
        "                print(f'RS_REC_FAIL try{_rec_try + 1} {_e}')\n"
        "                time.sleep(1.0)\n"
        "    state = {\n"
        "        'tick': 0, 'fired': False, 'hit': False,\n"
        "        'proj_actor': None, 'proj_dir': None, 'proj_pos': None,\n"
        "        'proj_vz': 0.0, 'proj_vz0': 0.0, 'proj_origin': (0.0, 0.0, 0.0),\n"
        "        'fire_time_actual': 0.0, 'hit_time': 0.0,\n"
        "        'result': 'NONE', 'closest_dist': 99999.0, 'closest_time': 0.0,\n"
        "        'closest_target_pos': (0,0,0), 'closest_proj_pos': (0,0,0),\n"
        "        'closest_speed': 0.0,\n"
        "    }\n"
        "    def target_pos(t):\n"
        "        x = TARGET_ORBIT_R * math.cos(TARGET_OMEGA * t + TARGET_PHASE)\n"
        "        y = TARGET_ORBIT_R * math.sin(TARGET_OMEGA * t + TARGET_PHASE)\n"
        "        z = TARGET_Z_BASE + TARGET_Z_AMP * math.sin(TARGET_Z_FREQ * TARGET_OMEGA * t + TARGET_PHASE)\n"
        "        return (x, y, z)\n"
        "    def arm_back_pos(t):\n"
        "        ang_deg = ARM_ANGULAR_SPEED * t + math.degrees(ARM_PHASE)\n"
        "        ang_rad = math.radians(ang_deg)\n"
        "        r = ARM_ORBIT_R + ARM_R_AMP * math.sin(ARM_R_FREQ * ang_rad)\n"
        "        bx = r * math.cos(ang_rad)\n"
        "        by = r * math.sin(ang_rad)\n"
        "        bz = TARGET_Z_BASE + ARM_Z_AMP * math.sin(ARM_Z_FREQ * ang_rad)\n"
        "        return (bx, by, bz)\n"
        "    def sim_tick(delta_time, elapsed_time, actors, p):\n"
        "        arm = actors[0]; tgt = actors[1]; pil = actors[2]\n"
        "        dt = delta_time\n"
        "        state['tick'] += 1\n"
        # Move target + pillar.
        "        tx, ty, tz = target_pos(elapsed_time)\n"
        "        pil_z = tz - (TARGET_Z_BASE - PILLAR_Z)\n"
        "        pil.K2_SetActorLocation(ur.CoreUObject.Vector(tx, ty, pil_z), False)\n"
        "        if not state['hit']:\n"
        "            tgt.K2_SetActorLocation(ur.CoreUObject.Vector(tx, ty, tz), False)\n"
        # Move arm; before fire it tracks target, after fire it locks to aim.
        "        bx, by, bz = arm_back_pos(elapsed_time)\n"
        "        to_target_yaw = math.degrees(math.atan2(ty - by, tx - bx))\n"
        "        if state['fired']:\n"
        "            vis_yaw = AIM_YAW; vis_pitch = AIM_PITCH\n"
        "        else:\n"
        "            vis_yaw = to_target_yaw; vis_pitch = 0.0\n"
        "        ay_rad = math.radians(vis_yaw); ap_rad = math.radians(vis_pitch)\n"
        "        cos_p = math.cos(ap_rad); sin_p = math.sin(ap_rad)\n"
        "        ax = bx + math.cos(ay_rad) * cos_p * (ARM_LENGTH * 0.5)\n"
        "        ay = by + math.sin(ay_rad) * cos_p * (ARM_LENGTH * 0.5)\n"
        "        az = bz + sin_p * (ARM_LENGTH * 0.5)\n"
        "        arm.K2_SetActorLocation(ur.CoreUObject.Vector(ax, ay, az), False)\n"
        "        ar = ur.CoreUObject.Rotator()\n"
        "        ar.Pitch = vis_pitch; ar.Yaw = vis_yaw; ar.Roll = 0.0\n"
        "        arm.K2_SetActorRotation(ar, False)\n"
        # Fire — spawn red projectile, store initial direction & speed.
        "        if not state['fired'] and FIRE_TIME >= 0 and elapsed_time >= FIRE_TIME:\n"
        "            state['fired'] = True\n"
        "            state['fire_time_actual'] = elapsed_time\n"
        "            ay_r = math.radians(AIM_YAW); pr = math.radians(AIM_PITCH)\n"
        "            cp = math.cos(pr); sp = math.sin(pr)\n"
        "            tip_x = bx + math.cos(ay_r) * cp * ARM_LENGTH\n"
        "            tip_y = by + math.sin(ay_r) * cp * ARM_LENGTH\n"
        "            tip_z = bz + sp * ARM_LENGTH\n"
        "            dx = math.cos(ay_r) * cp; dy = math.sin(ay_r) * cp; dz = sp\n"
        "            state['proj_dir'] = (dx, dy, dz)\n"
        "            state['proj_pos'] = [tip_x, tip_y, tip_z]\n"
        "            state['proj_origin'] = (tip_x, tip_y, tip_z)\n"
        "            state['proj_vz0'] = sp * PROJ_V0\n"
        "            state['proj_vz'] = sp * PROJ_V0\n"
        "            pt = ur.CoreUObject.Transform()\n"
        "            pt.Translation = ur.CoreUObject.Vector(tip_x, tip_y, tip_z)\n"
        "            pt.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(\n"
        "                ur.CoreUObject.Rotator(0,0,0))\n"
        "            psv = ur.CoreUObject.Vector(); psv.X = psv.Y = psv.Z = PROJ_SCALE\n"
        "            pt.Scale3D = psv\n"
        "            proj = world.SpawnActorEx(ur.Engine.StaticMeshActor, pt, 1)\n"
        "            if proj:\n"
        "                proj.SetActorLabel('RS_Projectile')\n"
        "                pc = proj.StaticMeshComponent\n"
        "                pc.SetMobility(2)\n"
        "                m = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(\n"
        "                    '/Engine/BasicShapes/Sphere.Sphere')\n"
        "                pc.SetStaticMesh(m)\n"
        "                pc.SetCollisionEnabled(0)\n"
        "                _pbm = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,\n"
        "                    '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial')\n"
        "                if _pbm:\n"
        "                    pc.SetMaterial(0, _pbm)\n"
        "                    pmid = pc.CreateDynamicMaterialInstance(0)\n"
        "                    pcol = ur.CoreUObject.LinearColor()\n"
        "                    pcol.R, pcol.G, pcol.B, pcol.A = 0.9, 0.1, 0.05, 1.0\n"
        "                    if pmid: pmid.SetVectorParameterValue('Color', pcol)\n"
        "                state['proj_actor'] = proj\n"
        "            state['fire_arm_pos'] = (bx, by, bz)\n"
        "            state['fire_target_pos'] = (tx, ty, tz)\n"
        "            state['fire_dist'] = math.sqrt((tx-bx)**2 + (ty-by)**2 + (tz-bz)**2)\n"
        # Move projectile.
        "        if state['fired'] and not state['hit'] and state['proj_actor']:\n"
        "            pdx, pdy, pdz = state['proj_dir']\n"
        "            pp = state['proj_pos']\n"
        "            dt_flight = elapsed_time - state['fire_time_actual']\n"
        # Evaluate the published closed form rather than accumulating
        # per-frame steps. Left-rectangle integration of an
        # exponentially decaying speed overshoots by ~v0*dt/2 — 21 cm at
        # 60 fps for a typical seed, half the 45 cm hit radius, and it
        # varies with frame rate. Evaluating s(dt) directly makes the
        # simulated trajectory identical to the equation the model is
        # given, by construction and independent of tick rate.
        "            cur_speed = PROJ_V0 * math.exp(-PROJ_DECAY_K * dt_flight)\n"
        "            s_h = (PROJ_V0 / PROJ_DECAY_K) * (1.0 - math.exp(-PROJ_DECAY_K * dt_flight))\n"
        "            ox, oy, oz = state['proj_origin']\n"
        "            pp[0] = ox + pdx * s_h\n"
        "            pp[1] = oy + pdy * s_h\n"
        # Vertical: pure ballistic, matching the published closed form
        # z(dt) = z_fire + vz_0*dt - 0.5*g*dt^2. The horizontal decay
        # factor must NOT appear here — it used to, which damped the
        # gravity-accumulated vz and put the simulated apex 725 cm away
        # from the equation the model is asked to solve (45 cm hit
        # radius). With the exp gone this increment is the exact
        # analytic difference z(t+dt) - z(t), so there is no
        # integration bias either.
        "            pp[2] = oz + state['proj_vz0'] * dt_flight - 0.5 * PROJ_GRAVITY * dt_flight * dt_flight\n"
        "            state['proj_vz'] = state['proj_vz0'] - PROJ_GRAVITY * dt_flight\n"
        "            state['proj_actor'].K2_SetActorLocation(\n"
        "                ur.CoreUObject.Vector(pp[0], pp[1], pp[2]), False)\n"
        "            ctx, cty, ctz = target_pos(elapsed_time)\n"
        "            ddx = pp[0] - ctx; ddy = pp[1] - cty; ddz = pp[2] - ctz\n"
        "            d = math.sqrt(ddx*ddx + ddy*ddy + ddz*ddz)\n"
        "            if d < state['closest_dist']:\n"
        "                state['closest_dist'] = d\n"
        "                state['closest_time'] = elapsed_time\n"
        "                state['closest_target_pos'] = (ctx, cty, ctz)\n"
        "                state['closest_proj_pos'] = (pp[0], pp[1], pp[2])\n"
        "                state['closest_speed'] = cur_speed\n"
        "            if d < HIT_RADIUS:\n"
        "                state['hit'] = True\n"
        "                state['hit_time'] = elapsed_time\n"
        "                state['result'] = 'HIT'\n"
        "                tc = tgt.StaticMeshComponent\n"
        "                tc.SetCollisionProfileName('PhysicsActor')\n"
        "                tc.SetMassOverrideInKg('', 5.0, True)\n"
        "                tc.SetSimulatePhysics(True)\n"
        "                tc.SetEnableGravity(True)\n"
        "                impv = ur.CoreUObject.Vector(\n"
        "                    pdx * IMPULSE_MAG, pdy * IMPULSE_MAG,\n"
        "                    pdz * IMPULSE_MAG + IMPULSE_MAG * 0.3)\n"
        "                tc.WakeAllRigidBodies()\n"
        "                tc.AddImpulse(impv, '', False)\n"
        "                state['proj_actor'].K2_DestroyActor()\n"
        "                state['proj_actor'] = None\n"
        "            elif cur_speed < PROJ_STALL_SPEED:\n"
        "                state['result'] = 'MISS'\n"
        "                state['hit_time'] = elapsed_time\n"
        "                state['proj_actor'].K2_DestroyActor()\n"
        "                state['proj_actor'] = None\n"
        # End conditions.
        "        if FIRE_TIME < 0 and elapsed_time >= OBSERVE_TIME:\n"
        "            state['result'] = 'OBSERVE'; _finish(); return False\n"
        "        if state['result'] in ('HIT', 'MISS'):\n"
        "            if elapsed_time > state['hit_time'] + 2.0:\n"
        "                _finish(); return False\n"
        "        if elapsed_time > MAX_SIM_TIME:\n"
        "            if not state['fired']: state['result'] = 'NO_FIRE'\n"
        "            elif state['result'] == 'NONE': state['result'] = 'MISS'\n"
        "            _finish(); return False\n"
        "        return True\n"
        "    def _finish():\n"
        "        try:\n"
        "            rec = ur.StopRecording()\n"
        "            if rec:\n"
        "                builtins._RS_VIDEO_PATH = rec.get('full_path', '')\n"
        "                builtins._RS_VIDEO_FILENAME = rec.get('filename', '')\n"
        "        except Exception:\n"
        "            pass\n"
        "        builtins._RS_RESULT = state['result']\n"
        "        builtins._RS_TICKS = state['tick']\n"
        "        def _rot_xy(x, y):\n"
        "            c = math.cos(FRAME_ROT); s = math.sin(FRAME_ROT)\n"
        "            return (x*c + y*s, -x*s + y*c)\n"
        "        lines = [\n"
        "            f'# Round {RUN_ID} Log',\n"
        "            f\"result: {state['result']}\",\n"
        "            f'fire_time_requested: {FIRE_TIME}',\n"
        "            f\"fire_time_actual: {state['fire_time_actual']:.3f}\",\n"
        "            f'aim_yaw: {AIM_YAW}',\n"
        "            f'aim_pitch: {AIM_PITCH}',\n"
        "        ]\n"
        "        if state['fired']:\n"
        "            ap = state.get('fire_arm_pos', (0,0,0))\n"
        "            tp = state.get('fire_target_pos', (0,0,0))\n"
        "            rap = _rot_xy(ap[0], ap[1])\n"
        "            rtp = _rot_xy(tp[0], tp[1])\n"
        "            cp = state['closest_proj_pos']\n"
        "            ct = state['closest_target_pos']\n"
        "            rcp = _rot_xy(cp[0], cp[1])\n"
        "            rct = _rot_xy(ct[0], ct[1])\n"
        "            lines += [\n"
        "                f'arm_pos_at_fire: ({rap[0]:.1f}, {rap[1]:.1f}, {ap[2]:.1f})',\n"
        "                f'target_pos_at_fire: ({rtp[0]:.1f}, {rtp[1]:.1f}, {tp[2]:.1f})',\n"
        "                f\"distance_at_fire: {state.get('fire_dist', 0):.1f}\",\n"
        "                f'aim_yaw_used: {AIM_YAW:.2f}',\n"
        "                f\"closest_approach_dist: {state['closest_dist']:.1f}\",\n"
        "                f\"closest_approach_time: {state['closest_time']:.3f}\",\n"
        "                f\"closest_approach_speed: {state['closest_speed']:.1f}\",\n"
        "                f'closest_target_pos: ({rct[0]:.1f}, {rct[1]:.1f}, {ct[2]:.1f})',\n"
        "                f'closest_proj_pos: ({rcp[0]:.1f}, {rcp[1]:.1f}, {cp[2]:.1f})',\n"
        "            ]\n"
        "        try:\n"
        "            os.makedirs(LOG_DIR, exist_ok=True)\n"
        "            with open(os.path.join(LOG_DIR, 'round_log.txt'), 'w') as lf:\n"
        "                lf.write('\\n'.join(lines))\n"
        "        except Exception:\n"
        "            pass\n"
        "        print(f\"RS_DONE result={state['result']} ticks={state['tick']}\")\n"
        "    ur.submit_tick_task(f'rs_run_{RUN_ID}', sim_tick,\n"
        "        [arm_actor, target_actor, pillar_actor], {},\n"
        "        max_duration=MAX_SIM_TIME + 5.0)\n"
        "    print(f'RS_TICK_SUBMITTED run={RUN_ID}')\n"
    )


# ---------------------------------------------------------------------------
# Polling — read the RS_* builtins from UE side.
# ---------------------------------------------------------------------------
READ_RESULT_CODE = (
    "import builtins\n"
    "print('RS_RESULT:' + str(getattr(builtins, '_RS_RESULT', '') or ''))\n"
    "print('RS_VIDEO:' + str(getattr(builtins, '_RS_VIDEO_PATH', '') or ''))\n"
    "print('RS_VIDEO_NAME:' + str(getattr(builtins, '_RS_VIDEO_FILENAME', '') or ''))\n"
    "print('RS_TICKS:' + str(getattr(builtins, '_RS_TICKS', 0)))\n"
)


def parse_result(stdout: str) -> dict:
    out = {"result": "", "video_path": "", "video_filename": "", "ticks": 0}
    for line in stdout.splitlines():
        if line.startswith("RS_RESULT:"):
            out["result"] = line[len("RS_RESULT:"):].strip()
        elif line.startswith("RS_VIDEO:"):
            out["video_path"] = line[len("RS_VIDEO:"):].strip()
        elif line.startswith("RS_VIDEO_NAME:"):
            out["video_filename"] = line[len("RS_VIDEO_NAME:"):].strip()
        elif line.startswith("RS_TICKS:"):
            try:
                out["ticks"] = int(line[len("RS_TICKS:"):].strip())
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------
# Stdout / response parsing
# ---------------------------------------------------------------------------
def unwrap(resp) -> str:
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


def write_episode_stub(workspace: Path, **fields) -> None:
    """Drop a placeholder ``episode_summary.json`` before round 1.

    A ``try/finally`` cannot record an episode whose *process* dies, and
    on 2026-07-25 that is precisely what happened: the harness blocked
    forever in ``recv()`` on a wedged UE, was killed, and left no trace
    at all — the run simply vanished from the results tree instead of
    showing up as a failure. Writing the stub up front means the only
    way an episode has no record is if its directory was never created.

    The real summary overwrites this file on normal completion, so a
    surviving ``final_status == "UNFINISHED"`` is an unambiguous marker
    that the episode died mid-flight. Tallies must exclude it rather
    than silently counting it as a MISS.
    """
    stub = {"final_status": "UNFINISHED", "crashed": True,
            "rounds": 0, "trajectory": [], **fields}
    (workspace / "episode_summary.json").write_text(
        json.dumps(stub, indent=2, default=str), encoding="utf-8")


def vlm_call_info(vlm) -> dict:
    """Transport-level diagnostics for the most recent VLM call.

    A response cut off at the token limit and a response that is simply
    badly formatted are indistinguishable once ``finish_reason`` has
    been discarded — which is how the 2026-07-25 truncations were
    misread as reasoning failures. Returns ``{}`` for backends that do
    not report it. Lives here (not in a per-arm ``task.py``) so every
    harness arm records the same fields.
    """
    fr = getattr(vlm, "last_finish_reason", "") or ""
    if not fr:
        return {}
    info: dict = {"finish_reason": fr,
                  "parts": getattr(vlm, "last_part_count", 0)}
    usage = getattr(vlm, "last_usage", None)
    # gemini-direct hands back an SDK object, the OpenAI-compatible
    # transport a plain dict — read both, and keep whichever token
    # counters that backend happens to report.
    for attr in ("prompt_token_count", "candidates_token_count",
                 "thoughts_token_count", "total_token_count",
                 "prompt_tokens", "completion_tokens", "total_tokens"):
        v = (usage.get(attr) if isinstance(usage, dict)
             else getattr(usage, attr, None))
        if v is not None:
            info[attr] = v
    return info


def is_truncated(info: dict) -> bool:
    """True when the last call hit the output-token ceiling."""
    return "MAX_TOKENS" in str(info.get("finish_reason", ""))


def parse_response(resp: str) -> tuple[str, str, Optional[float], Optional[float], Optional[float]]:
    """Pull observation/knowledge + fire_time/aim_yaw/aim_pitch from
    a free-form response. Returns ('', '', None, None, None) on full
    parse failure (caller decides how to handle)."""
    observation = ""
    knowledge = ""
    ft = aim_yaw = aim_pitch = None
    for line in resp.split("\n"):
        low = line.strip().lower()
        if low.startswith("observation:"):
            observation = line.strip()[len("observation:"):].strip()
        elif low.startswith("analysis:"):
            observation = line.strip()[len("analysis:"):].strip()
        elif low.startswith("knowledge:"):
            knowledge = line.strip()[len("knowledge:"):].strip()
        elif low.startswith("fire_time:"):
            try:
                ft = float(re.search(r'[-\d.]+', line.split(":", 1)[1]).group())
            except Exception:
                pass
        elif low.startswith("aim_yaw:"):
            try:
                aim_yaw = float(re.search(r'[-\d.]+', line.split(":", 1)[1]).group())
            except Exception:
                pass
        elif low.startswith("aim_pitch:"):
            try:
                aim_pitch = float(re.search(r'[-\d.]+', line.split(":", 1)[1]).group())
            except Exception:
                pass
    if ft is None or aim_yaw is None or aim_pitch is None:
        m = re.search(
            r'fire\w*\(\s*(?:fire_time\s*=\s*)?([-\d.]+)\s*,\s*'
            r'(?:aim_yaw\s*=\s*)?([-\d.]+)\s*,\s*'
            r'(?:pitch\s*=\s*)?([-\d.]+)',
            resp,
        )
        if m:
            if ft is None: ft = float(m.group(1))
            if aim_yaw is None: aim_yaw = float(m.group(2))
            if aim_pitch is None: aim_pitch = float(m.group(3))
    # NOTE: do not add a "scrape any number that follows fire_time
    # anywhere in the text" fallback here. It was tried on 2026-07-25
    # and recovered zero of the four truncated rounds (they are cut off
    # before any value is emitted), while on the one round that did
    # contain a mid-reasoning "fire_time = 5.0" it scraped a candidate
    # the model went on to explicitly reject two lines later — feeding a
    # wrong value into the next round's knowledge block. The real defect
    # was output-token truncation, not format rigidity.
    if not observation:
        observation = (
            resp.split("knowledge:")[0].strip()[:400]
            if "knowledge:" in resp.lower() else resp[:400]
        )
    if not knowledge:
        km = re.search(
            r'knowledge:\s*(.*?)(?:fire_time:|```|$)',
            resp, re.DOTALL | re.IGNORECASE,
        )
        if km:
            knowledge = km.group(1).strip()[:400]
    return observation, knowledge, ft, aim_yaw, aim_pitch


# ---------------------------------------------------------------------------
# Video harvest — copy the recorded h264/mp4 from UE's Saved/ dir
# into the workspace so the next prompt can attach it.
# ---------------------------------------------------------------------------
PACKAGE_RECORD_DIR = None


def _remux_to_mp4(src: Path, dst: Path) -> Optional[Path]:
    """Wrap UE's raw capture in a real mp4 container.

    UE's recorder emits a bare Annex-B h264 elementary stream. Renaming
    it to ``.mp4`` produces a file ffprobe *will* answer questions about
    — with wrong answers: no container timing means the reported
    duration came out at exactly half the truth, so the six sampled
    frames covered only 4.6%-46.5% of each round and every impact after
    ~4.6 s was invisible to the vision and combined arms. Remuxing with
    ``-c:v copy`` re-timestamps without re-encoding, so the pixels are
    untouched.

    The source is copied first (UE may still hold a handle) and is never
    deleted — ``Saved/Recordings`` is shared with other tasks and with
    parallel instances.
    """
    tmp = dst.with_name(dst.stem + "_raw" + src.suffix)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(6):
            try:
                shutil.copy2(src, tmp)
                break
            except FileNotFoundError:
                return None
            except PermissionError:
                if attempt == 5:
                    return None
                time.sleep(1.0)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(tmp),
             "-c:v", "copy", str(dst)],
            capture_output=True, timeout=120,
        )
        if not dst.exists() or dst.stat().st_size == 0:
            log.error("ffmpeg remux produced no output for %s — is ffmpeg on "
                      "PATH?", src)
            return None
        return dst
    except Exception as e:  # noqa: BLE001
        log.error("remux of %s failed: %s", src, e)
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def harvest_video(
    src_path: str,
    dst: Path,
    *,
    package_record_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Remux the recorded h264/mp4 from UE's Saved/ dir into ``dst``.

    Uses only the exact path returned by this engine. Returns the
    destination path on success and None on failure. Frame-grid callers
    abort unscored if their required visual feedback is unavailable.
    """
    src = Path(src_path) if src_path else None
    if src and src.exists():
        # UE6 bridge (PyCapture6): the colour channel is a lossless .awraw plane stream;
        # encode it on the host instead of remuxing an h264 elementary stream.
        if src.suffix.lower() == ".awraw":
            try:
                from veriworld.infra.ue6.awraw import to_mp4
                dst.parent.mkdir(parents=True, exist_ok=True)
                return to_mp4(src, dst, fps=30)
            except Exception as e:  # noqa: BLE001
                log.error("awraw->mp4 encode of %s failed: %s", src, e)
                return None
        out = _remux_to_mp4(src, dst)
        if out is not None:
            return out
        log.warning("remux failed for %s; no unrelated recording will be substituted", src)
    elif not src_path:
        # Empty src means UE never reported a recording — StartRecording
        # failed (see the RS_REC_FAIL retry in tick_runner_code). Say so:
        # this exact condition dropped 55% of round-1 grids on 2026-07-25
        # while the logs stayed pristine.
        log.error("no recording path from UE for %s — StartRecording "
                  "failed in-engine; this round has no visual channel", dst.name)
    return None


def read_round_log(workspace: Path) -> str:
    p = workspace / "round_log.txt"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------------------
# Convenience: switch to Untitled level + spawn scene.
# ---------------------------------------------------------------------------
async def open_level_and_setup(engine, params: dict) -> None:
    out = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_SWITCHING" in out:
        await asyncio.sleep(5.0)
    confirm = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_READY" not in confirm:
        log.warning("level didn't settle: %r", confirm.strip())
    await asyncio.sleep(0.8)
    out = unwrap(await engine.python_exec(setup_scene_code(params)))
    log.info("setup_scene: %s", out.strip().split('\n')[-1])
    await asyncio.sleep(0.6)


async def run_round(
    engine,
    params: dict,
    fire_time: float,
    aim_yaw: float,
    aim_pitch: float,
    run_id: int,
    workspace: Path,
    *,
    record: bool = True,
    poll_interval_s: float = 1.0,
    poll_timeout_s: float = 35.0,
) -> dict:
    """Submit one round's tick task, poll for result, harvest video.
    Returns a dict with: result, ticks, video_path (Path|None), log."""
    await engine.python_exec(CLEAN_CODE)
    await asyncio.sleep(0.2)
    await engine.python_exec(setup_scene_code(params))
    await asyncio.sleep(0.4)
    _sub = unwrap(await engine.python_exec(tick_runner_code(
        params, fire_time, aim_yaw, aim_pitch, run_id, str(workspace),
        record=record)))
    # The submission stdout carries RS_REC_START / RS_REC_FAIL. Discarding
    # it (as this code used to) is how the recorder's actual exception
    # stayed invisible through two rounds of debugging — the harness knew
    # THAT StartRecording failed but never WHY.
    if record and "RS_REC_FAIL" in _sub:
        for _line in _sub.splitlines():
            if "RS_REC_FAIL" in _line:
                log.error("round %d recorder: %s", run_id, _line.strip()[:300])

    deadline = asyncio.get_event_loop().time() + poll_timeout_s
    parsed = {"result": "", "video_path": "", "ticks": 0}
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval_s)
        out = unwrap(await engine.python_exec(READ_RESULT_CODE))
        parsed = parse_result(out)
        if parsed["result"] and parsed["result"] != "NONE":
            break
    if parsed["result"] not in ("OBSERVE", "HIT", "MISS"):
        raise RuntimeError("MovingShooter runtime did not return a valid terminal verdict; episode is unscored: " + str(parsed["result"]))

    video_dst: Optional[Path] = None
    if record:
        await asyncio.sleep(1.0)  # let UE finish the file
        dst = workspace / f"round_{run_id:02d}_video.mp4"
        video_dst = harvest_video(parsed.get("video_path", ""), dst)

    log_text = read_round_log(workspace)
    return {
        "result": parsed["result"],
        "ticks": parsed.get("ticks", 0),
        "video_path": video_dst,
        "log": log_text,
    }


__all__ = [
    "LEVEL_SWITCH_CODE", "CLEAN_CODE", "READ_RESULT_CODE",
    "setup_scene_code", "tick_runner_code",
    "parse_result", "parse_response", "unwrap",
    "harvest_video", "read_round_log",
    "open_level_and_setup", "run_round",
    "PACKAGE_RECORD_DIR",
]
