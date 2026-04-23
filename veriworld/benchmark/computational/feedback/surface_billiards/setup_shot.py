"""Harness-internal shot template for SurfaceBilliards.

Not an agent-facing file. The harness reads this template, substitutes
``{{V_ANGLE}}`` and ``{{V_SPEED}}`` with the values the VLM returned,
and executes the result inside UE. Includes the full scene (terrain +
ball + A marker + A/B labels + target disc + camera) and the tick task
that logs PASS/FAIL to ``lean_verify/log_for_verify.txt``.

Keeping this fixed on the harness side guarantees visual consistency
(A green marker + both text labels present in every round's video) and
frees the agent to focus on the two scalars that actually matter.

The log file also records ``time.time()`` wall-clock per tick, which
the harness subtracts from its recorded video-start wall-clock to map
physics-log times to video-playback times (NVENC drops frames under
GPU load, so tick-elapsed and video-elapsed diverge by several seconds).
"""
import json
import math
import os
import time as _time

import unreal_runtime as ur

BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/unreal_projects_lean/lean/unit_tests/17_surface_billiards_hard"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N = params["grid_n"]
SPACING = params["grid_spacing"]
BALL_R = params["ball_radius"]
BASE_Z = params["surface_z_base"]
GAUSSIANS = params["gaussians"]
A = params["start"]
B = params["target"]
TARGET_R = B["radius"]
MAX_SPEED = params["max_speed"]
PITCH = params["pitch"]

SHADER_PATH = os.path.join(BASE, "lean_verify", "bouncy_ball.slang")
LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")


def eval_surface(x, y):
    z = BASE_Z
    for g in GAUSSIANS:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx * dx + dy * dy) / (2 * g["sigma"] ** 2))
    return z


# Agent-supplied scalars — substituted by the harness before exec.
v_angle = {{V_ANGLE}}
v_speed = max(0.0, min({{V_SPEED}}, MAX_SPEED))

cos_p, sin_p = math.cos(PITCH), math.sin(PITCH)
vx = v_speed * math.cos(v_angle) * cos_p
vy = v_speed * math.sin(v_angle) * cos_p
vz = v_speed * sin_p

world = ur.Engine.GetDefaultWorld()
slang = ur.SlangCudaPlugin.SlangCudaBlueprintLibrary

# ── Clean previous scene ──
if hasattr(ur.ChaosHelper, 'GPUClothActor'):
    for a in world.GetActorsOfClass(ur.ChaosHelper.GPUClothActor):
        a.K2_DestroyActor()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("SB_"):
        a.K2_DestroyActor()
for a in world.GetActorsOfClass(ur.Engine.TextRenderActor):
    if a.GetActorLabel().startswith("SB_"):
        a.K2_DestroyActor()

# ── Cloth surface ──
ct = ur.CoreUObject.Transform()
ct.Translation = ur.CoreUObject.Vector(0, 0, 0)
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, 0, 0))
ct.Rotation = quat
sv = ur.CoreUObject.Vector(); sv.X = sv.Y = sv.Z = 1.0
ct.Scale3D = sv
cloth = world.SpawnActorEx(ur.ChaosHelper.GPUClothActor, ct, 1)
cloth.SetActorLabel("SB_Surface")
cloth.InitCloth(GRID_N, SPACING, 800.0, 8.0, 3)
cloth.SetShader(SHADER_PATH, "BouncyBall")

mat = ur.StaticLoadObject(
    ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
root = cloth.K2_GetRootComponent()
if root and mat:
    root.SetMaterial(0, mat)

# ── Build terrain; launch ball at A with velocity ──
floats = slang.ReadBackFloatBuffer("nodes")
expected = GRID_N * GRID_N * 8
if floats and len(floats) >= expected:
    for gi in range(GRID_N):
        for gj in range(GRID_N):
            idx = gi * GRID_N + gj
            base = idx * 8
            x = (gi - GRID_N / 2.0) * SPACING
            y = (gj - GRID_N / 2.0) * SPACING
            z = eval_surface(x, y)
            floats[base + 0] = x
            floats[base + 1] = y
            floats[base + 2] = z
            floats[base + 3] = 0.0
    floats[5 * 8 + 7] = A["x"]
    floats[6 * 8 + 7] = A["y"]
    floats[7 * 8 + 7] = A["z"]
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = vx
    floats[10 * 8 + 7] = vy
    floats[11 * 8 + 7] = vz
    slang.UploadFloatArray("nodes", floats)

# ── Visual ball ──
sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")

bt = ur.CoreUObject.Transform()
bt.Translation = ur.CoreUObject.Vector(A["x"], A["y"], A["z"])
bt.Rotation = quat
bsv = ur.CoreUObject.Vector(); bsv.X = bsv.Y = bsv.Z = BALL_R / 50.0
bt.Scale3D = bsv
ball = world.SpawnActorEx(ur.Engine.StaticMeshActor, bt, 1)
ball.SetActorLabel("SB_Ball")
bc = ball.StaticMeshComponent
bc.SetMobility(2); bc.SetStaticMesh(sphere); bc.SetCollisionEnabled(0)

# ── A launch marker ──
label_rot = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, -90, 0))
mt = ur.CoreUObject.Transform()
mt.Translation = ur.CoreUObject.Vector(A["x"], A["y"], A["z"] + 20)
mt.Rotation = quat
msv = ur.CoreUObject.Vector(); msv.X = msv.Y = msv.Z = 0.2
mt.Scale3D = msv
marker_a = world.SpawnActorEx(ur.Engine.StaticMeshActor, mt, 1)
marker_a.SetActorLabel("SB_MarkerA")
mc = marker_a.StaticMeshComponent
mc.SetMobility(2); mc.SetStaticMesh(sphere); mc.SetCollisionEnabled(0)

a_label_t = ur.CoreUObject.Transform()
a_label_t.Translation = ur.CoreUObject.Vector(A["x"], A["y"], A["z"] + 60)
a_label_t.Rotation = label_rot
a_label_t.Scale3D = ur.CoreUObject.Vector(1, 1, 1)
a_label = world.SpawnActorEx(ur.Engine.TextRenderActor, a_label_t, 1)
if a_label:
    a_label.SetActorLabel("SB_LabelA")
    atc = a_label.TextRender
    atc.K2_SetText("A (Launch)")
    atc.WorldSize = 30.0
    atc.SetTextRenderColor(ur.CoreUObject.Color(0, 255, 0, 255))
    atc.HorizontalAlignment = ur.Engine.EHorizTextAligment.EHTA_Center
    atc.VerticalAlignment = ur.Engine.EVerticalTextAligment.EVRTA_TextCenter

# ── B target disc + label ──
target_z = eval_surface(B["x"], B["y"]) + 1.0
tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(B["x"], B["y"], target_z)
tt.Rotation = quat
tsv = ur.CoreUObject.Vector()
tsv.X = TARGET_R / 30.0; tsv.Y = TARGET_R / 30.0; tsv.Z = 0.02
tt.Scale3D = tsv
ta = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
ta.SetActorLabel("SB_Target")
tc = ta.StaticMeshComponent
tc.SetMobility(2); tc.SetStaticMesh(cyl); tc.SetCollisionEnabled(0)

b_label_t = ur.CoreUObject.Transform()
b_label_t.Translation = ur.CoreUObject.Vector(
    B["x"], B["y"], eval_surface(B["x"], B["y"]) + 60)
b_label_t.Rotation = label_rot
b_label_t.Scale3D = ur.CoreUObject.Vector(1, 1, 1)
b_label = world.SpawnActorEx(ur.Engine.TextRenderActor, b_label_t, 1)
if b_label:
    b_label.SetActorLabel("SB_LabelB")
    btc = b_label.TextRender
    btc.K2_SetText("B (Target)")
    btc.WorldSize = 30.0
    btc.SetTextRenderColor(ur.CoreUObject.Color(255, 50, 50, 255))
    btc.HorizontalAlignment = ur.Engine.EHorizTextAligment.EHTA_Center
    btc.VerticalAlignment = ur.Engine.EVerticalTextAligment.EVRTA_TextCenter

# ── Camera: side view across trajectory ──
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns:
    pawn = pawns[0]
    ctrl = pawn.GetController()
    cam_x = (A["x"] + B["x"]) / 2.0
    cam_y = min(A["y"], B["y"]) - 500.0
    cam_z = A["z"] + 100.0
    pawn.K2_SetActorLocation(ur.CoreUObject.Vector(cam_x, cam_y, cam_z), False)
    rot = ur.CoreUObject.Rotator()
    rot.Pitch = -25.0
    rot.Yaw = 90.0
    ctrl.SetControlRotation(rot)

# ── Tick task: sync ball, detect settle, log PASS/FAIL ──
log_file = open(LOG_PATH, "w", encoding="utf-8")
log_file.write("frame,elapsed,wallclock,bx,by,bz,dist_to_target,status\n")


def sync_ball(delta_time, elapsed_time, actors, ctx):
    fl = slang.ReadBackFloatBuffer("nodes")
    if fl and len(fl) > 11 * 8 + 7:
        bx = fl[5 * 8 + 7]; by = fl[6 * 8 + 7]; bz = fl[7 * 8 + 7]
        ctx["ball"].K2_SetActorLocation(ur.CoreUObject.Vector(bx, by, bz), False)
        ctx["frame"] = ctx.get("frame", 0) + 1
        dist = math.sqrt((bx - B["x"]) ** 2 + (by - B["y"]) ** 2)
        now = _time.time()
        if ctx["frame"] % 30 == 0:
            ctx["log"].write(
                f"{ctx['frame']},{elapsed_time:.3f},{now:.6f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},flying\n")
        if not ctx.get("done"):
            prev = ctx.get("prev_pos")
            if prev and abs(bx - prev[0]) < 0.2 and abs(by - prev[1]) < 0.2 and abs(bz - prev[2]) < 0.2:
                ctx["settled_count"] = ctx.get("settled_count", 0) + 1
            else:
                ctx["settled_count"] = 0
            ctx["prev_pos"] = (bx, by, bz)
            if ctx["settled_count"] > 30:
                ctx["done"] = True
                result = "PASS" if dist <= TARGET_R else "FAIL"
                ctx["log"].write(
                    f"{ctx['frame']},{elapsed_time:.3f},{now:.6f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},settled\n")
                ctx["log"].write(f"\n# RESULT: {result}\n")
                ctx["log"].flush(); ctx["log"].close()
                print(f"Ball settled at ({bx:.1f}, {by:.1f}, {bz:.1f})  dist={dist:.1f}  RESULT={result}")
                return False
    return True


ur.submit_tick_task("sb_sync", sync_ball, [],
                    {"ball": ball, "frame": 0, "settled_count": 0, "log": log_file},
                    max_duration=60.0)
print(f"[SHOT] v_angle={math.degrees(v_angle):.1f}deg  v_speed={v_speed:.1f}  pitch={math.degrees(PITCH):.1f}deg")
print(f"       vx={vx:.1f}  vy={vy:.1f}  vz={vz:.1f}")
