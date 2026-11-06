"""Example: default shot from api.md template (NOT a solution)."""
import unreal_runtime as ur
import json, os, math

BASE = __PAPER_CASE_DIR__
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N     = params["grid_n"]
SPACING    = params["grid_spacing"]
BALL_R     = params["ball_radius"]
BASE_Z     = params["surface_z_base"]
GAUSSIANS  = params["gaussians"]
TARGET     = params["target"]
TARGET_R   = TARGET["radius"]
FRICTION   = params["friction"]
START_X    = params["start"]["x"]
START_Y    = params["start"]["y"]
MAX_SPEED  = params["max_speed"]

SHADER_PATH = os.path.join(BASE, "lean_verify", "billiard_ball.slang")

def eval_surface(x, y):
    z = BASE_Z
    for g in GAUSSIANS:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx*dx + dy*dy) / (2 * g["sigma"]**2))
    return z

# =============================================
# YOUR SHOT — only change these 2 values
# Start position is FIXED at (START_X, START_Y)
# speed capped at MAX_SPEED
# You do NOT have target coordinates. Estimate from VIDEO.
# You do NOT have the terrain formula. READ THE TERRAIN from the video.
# =============================================
v_angle = __AGENT_ANGLE__
v_speed = __AGENT_SPEED__

# Fixed start — no clamping needed
start_x = START_X
start_y = START_Y
v_speed = min(v_speed, MAX_SPEED)

ball_z = eval_surface(start_x, start_y) + BALL_R + 2.0
vx = v_speed * math.cos(v_angle)
vy = v_speed * math.sin(v_angle)

# ── Scene setup (DO NOT MODIFY below this line) ──

world = ur.Engine.GetDefaultWorld()
slang = ur.SlangCudaPlugin.SlangCudaBlueprintLibrary

if hasattr(ur.ChaosHelper, 'GPUClothActor'):
    for a in world.GetActorsOfClass(ur.ChaosHelper.GPUClothActor):
        a.K2_DestroyActor()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("SB_"):
        a.K2_DestroyActor()

ct = ur.CoreUObject.Transform()
ct.Translation = ur.CoreUObject.Vector(0, 0, 0)
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, 0, 0))
ct.Rotation = quat
sv = ur.CoreUObject.Vector(); sv.X = sv.Y = sv.Z = 1.0
ct.Scale3D = sv
cloth = world.SpawnActorEx(ur.ChaosHelper.GPUClothActor, ct, 1)
cloth.SetActorLabel("SB_Cloth")
cloth.InitCloth(GRID_N, SPACING, 800.0, 8.0, 3)
cloth.SetShader(SHADER_PATH, "BilliardBall")

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

    floats[5 * 8 + 7] = start_x
    floats[6 * 8 + 7] = start_y
    floats[7 * 8 + 7] = ball_z
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = vx
    floats[10 * 8 + 7] = vy
    floats[11 * 8 + 7] = 0.0

    slang.UploadFloatArray("nodes", floats)

lib = ur.Engine.RuntimeMaterialLibrary
props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Masked", True)

lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.4, 0.55, 0.35);\n"
    "PixelMaterialInputs.Roughness = 0.7;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_SurfMat", props, True)
mat = lib.GetRuntimeMaterial("SB_SurfMat")
root = cloth.K2_GetRootComponent()
if root and mat and hasattr(root, 'SetMaterial'):
    root.SetMaterial(0, mat)

sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")

bt = ur.CoreUObject.Transform()
bt.Translation = ur.CoreUObject.Vector(start_x, start_y, ball_z)
bt.Rotation = quat
bsv = ur.CoreUObject.Vector(); bsv.X = bsv.Y = bsv.Z = BALL_R / 50.0
bt.Scale3D = bsv
ball = world.SpawnActorEx(ur.Engine.StaticMeshActor, bt, 1)
ball.SetActorLabel("SB_Ball")
bc = ball.StaticMeshComponent
bc.SetMobility(2)
bc.SetStaticMesh(sphere)
bc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.95, 0.95, 0.95);\n"
    "PixelMaterialInputs.Roughness = 0.1;",
    "SB_BallMat", props, True)
bmat = lib.GetRuntimeMaterial("SB_BallMat")
if bmat: bc.SetMaterial(0, bmat)

cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")
target_z = eval_surface(TARGET["x"], TARGET["y"]) + 1.0

tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(TARGET["x"], TARGET["y"], target_z)
tt.Rotation = quat
tsv = ur.CoreUObject.Vector()
tsv.X = TARGET_R / 30.0
tsv.Y = TARGET_R / 30.0
tsv.Z = 0.02
tt.Scale3D = tsv
ta = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
ta.SetActorLabel("SB_Target")
tc = ta.StaticMeshComponent
tc.SetMobility(2)
tc.SetStaticMesh(cyl)
tc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(5.0, 0.3, 0.3);\n"
    "PixelMaterialInputs.BaseColor = float3(1.0, 0.1, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.3;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_TargetMat", props, True)
tmat = lib.GetRuntimeMaterial("SB_TargetMat")
if tmat: tc.SetMaterial(0, tmat)

arrow_base_z = target_z + 20.0
shaft_t = ur.CoreUObject.Transform()
shaft_t.Translation = ur.CoreUObject.Vector(TARGET["x"], TARGET["y"], arrow_base_z + 60.0)
shaft_t.Rotation = quat
shaft_sv = ur.CoreUObject.Vector(); shaft_sv.X = 0.06; shaft_sv.Y = 0.06; shaft_sv.Z = 0.6
shaft_t.Scale3D = shaft_sv
shaft = world.SpawnActorEx(ur.Engine.StaticMeshActor, shaft_t, 1)
shaft.SetActorLabel("SB_ArrowShaft")
shaft_c = shaft.StaticMeshComponent; shaft_c.SetMobility(2)
shaft_c.SetStaticMesh(cyl); shaft_c.SetCollisionEnabled(0)
if tmat: shaft_c.SetMaterial(0, tmat)

cone = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cone.Cone")
head_t = ur.CoreUObject.Transform()
head_t.Translation = ur.CoreUObject.Vector(TARGET["x"], TARGET["y"], arrow_base_z + 10.0)
head_rot = ur.CoreUObject.Rotator(); head_rot.Roll = 180.0
head_quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(head_rot)
head_t.Rotation = head_quat
head_sv = ur.CoreUObject.Vector(); head_sv.X = 0.25; head_sv.Y = 0.25; head_sv.Z = 0.3
head_t.Scale3D = head_sv
arrow_head = world.SpawnActorEx(ur.Engine.StaticMeshActor, head_t, 1)
arrow_head.SetActorLabel("SB_ArrowHead")
head_c = arrow_head.StaticMeshComponent; head_c.SetMobility(2)
head_c.SetStaticMesh(cone); head_c.SetCollisionEnabled(0)
if tmat: head_c.SetMaterial(0, tmat)

# Single green sphere at fixed start point
start_z = eval_surface(START_X, START_Y) + BALL_R + 5.0
mt = ur.CoreUObject.Transform()
mt.Translation = ur.CoreUObject.Vector(START_X, START_Y, start_z)
mt.Rotation = quat
msv = ur.CoreUObject.Vector(); msv.X = 0.3; msv.Y = 0.3; msv.Z = 0.3
mt.Scale3D = msv
marker = world.SpawnActorEx(ur.Engine.StaticMeshActor, mt, 1)
marker.SetActorLabel("SB_StartMarker")
mc = marker.StaticMeshComponent
mc.SetMobility(2)
mc.SetStaticMesh(sphere)
mc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(0.5, 6.0, 0.5);\n"
    "PixelMaterialInputs.BaseColor = float3(0.1, 0.95, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.2;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_StartMat", props, True)
zmat = lib.GetRuntimeMaterial("SB_StartMat")
if zmat: mc.SetMaterial(0, zmat)

game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]
    controller = pawn.GetController()
    pawn.K2_SetActorLocation(
        ur.CoreUObject.Vector(0, -800, BASE_Z + 800), False)
    cam_rot = ur.CoreUObject.Rotator()
    cam_rot.Pitch = -40.0
    cam_rot.Yaw = 90.0
    controller.SetControlRotation(cam_rot)

LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")
log_file = open(LOG_PATH, "w", encoding="utf-8")
log_file.write("frame,elapsed,bx,by,bz,dist_to_target,status\n")

def sync_ball(delta_time, elapsed_time, actors, ctx):
    if elapsed_time >= 89.9:
        ctx['log'].write('\n# simulation_budget_exhausted\n# RESULT: FAIL\n')
        ctx['log'].flush()
        ctx['log'].close()
        return False
    fl = slang.ReadBackFloatBuffer("nodes")
    if fl and len(fl) > 11 * 8 + 7:
        bx = fl[5 * 8 + 7]
        by = fl[6 * 8 + 7]
        bz = fl[7 * 8 + 7]
        ctx["ball"].K2_SetActorLocation(
            ur.CoreUObject.Vector(bx, by, bz), False)
        ctx["frame"] = ctx.get("frame", 0) + 1
        dx = bx - TARGET["x"]
        dy = by - TARGET["y"]
        dist = math.sqrt(dx * dx + dy * dy)
        if ctx["frame"] % 10 == 0:
            ctx["log"].write(
                f"{ctx['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},flying\n")
        if not ctx.get("summarized"):
            prev = ctx.get("prev_pos")
            if prev and abs(bx - prev[0]) < 0.2 and abs(by - prev[1]) < 0.2:
                ctx["settled_count"] = ctx.get("settled_count", 0) + 1
            else:
                ctx["settled_count"] = 0
            ctx["prev_pos"] = (bx, by, bz)
            if ctx["settled_count"] > 30:
                ctx["summarized"] = True
                hit = dist <= TARGET_R
                status = "PASS" if hit else "FAIL"
                ctx["log"].write(
                    f"{ctx['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},LANDED_{status}\n")
                ctx["log"].write(f"\n# RESULT: {status}\n")
                ctx["log"].flush()
                ctx["log"].close()
                return False
    return True

ur.submit_tick_task("sb_sync", sync_ball, [],
                    {"ball": ball, "frame": 0, "log": log_file},
                    max_duration=90.0)
