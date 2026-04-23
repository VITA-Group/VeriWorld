"""
Example: create a wavy surface (NOT a solution to the task — just shows API usage).

This creates a sine-wave surface. The ball will roll around but NOT hit any target.
Use this to understand the code structure, then write your own surface shape.
"""
import unreal_runtime as ur
import math
import json
import os

BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/unreal_projects_lean/lean/unit_tests/12c_drop_to_target"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N    = params["grid_n"]
SPACING   = params["grid_spacing"]
BALL_R    = params["ball_radius"]
SURFACE_Z = params["surface_z"]
TARGET    = params["target"]
TARGET_R  = params["target_radius"]
SHADER_PATH = BASE + "/lean_verify/slide_ball.slang"
LOG_PATH  = BASE + "/lean_verify/log_for_verify.txt"

world = ur.Engine.GetDefaultWorld()
slang = ur.SlangCudaPlugin.SlangCudaBlueprintLibrary

# 1. Clean
if hasattr(ur.ChaosHelper, 'GPUClothActor'):
    for a in world.GetActorsOfClass(ur.ChaosHelper.GPUClothActor):
        a.K2_DestroyActor()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("SB_"):
        a.K2_DestroyActor()

# 2. Spawn GPUClothActor
ct = ur.CoreUObject.Transform()
ct.Translation = ur.CoreUObject.Vector(0, 0, 0)
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(ur.CoreUObject.Rotator(0, 0, 0))
ct.Rotation = quat
sv = ur.CoreUObject.Vector(); sv.X = sv.Y = sv.Z = 1.0
ct.Scale3D = sv
slide = world.SpawnActorEx(ur.ChaosHelper.GPUClothActor, ct, 1)
slide.SetActorLabel("SB_Slide")

# 3. InitCloth + SetShader
slide.InitCloth(GRID_N, SPACING, 800.0, 8.0, 3)
ok = slide.SetShader(SHADER_PATH, "SlideBall")
print(f"SetShader: {ok}")

# 4. Surface shape: sine wave (EXAMPLE ONLY — will NOT hit target)
floats = slang.ReadBackFloatBuffer("nodes")
expected = GRID_N * GRID_N * 8
if floats and len(floats) >= expected:
    for gi in range(GRID_N):
        for gj in range(GRID_N):
            idx = gi * GRID_N + gj
            base = idx * 8

            x = (gi - GRID_N / 2.0) * SPACING
            y = (gj - GRID_N / 2.0) * SPACING

            # Sine wave — just an example of how to set Z per node
            z = SURFACE_Z + 30.0 * math.sin(x / 80.0) * math.cos(y / 80.0)

            floats[base + 0] = x
            floats[base + 1] = y
            floats[base + 2] = z
            floats[base + 3] = 0.0  # invMass=0 (static)

    # Ball
    floats[5 * 8 + 7] = 0.0
    floats[6 * 8 + 7] = 0.0
    floats[7 * 8 + 7] = SURFACE_Z + BALL_R + 10
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = 0.0
    floats[10 * 8 + 7] = 0.0
    floats[11 * 8 + 7] = 0.0
    slang.UploadFloatArray("nodes", floats)
    print("Example wavy surface uploaded")

# 5-8: Material, ball visual, target, camera (same boilerplate as api.md)
lib = ur.Engine.RuntimeMaterialLibrary
props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Masked", True)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.3, 0.7, 0.4);\n"
    "PixelMaterialInputs.Roughness = 0.5;\nPixelMaterialInputs.OpacityMask = 1.0;",
    "SB_SlideMat", props, True)
mat = lib.GetRuntimeMaterial("SB_SlideMat")
root = slide.K2_GetRootComponent()
if root and mat and hasattr(root, 'SetMaterial'):
    root.SetMaterial(0, mat)

bt = ur.CoreUObject.Transform()
bt.Translation = ur.CoreUObject.Vector(0, 0, SURFACE_Z + BALL_R + 10)
bt.Rotation = quat
bsv = ur.CoreUObject.Vector(); bsv.X = bsv.Y = bsv.Z = BALL_R / 50.0
bt.Scale3D = bsv
ball = world.SpawnActorEx(ur.Engine.StaticMeshActor, bt, 1)
ball.SetActorLabel("SB_Ball")
bc = ball.StaticMeshComponent
bc.SetMobility(2)
sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath("/Engine/BasicShapes/Sphere.Sphere")
bc.SetStaticMesh(sphere)
bc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.9, 0.2, 0.1); PixelMaterialInputs.Roughness = 0.15;",
    "SB_BallMat", props, True)
bmat = lib.GetRuntimeMaterial("SB_BallMat")
if bmat: bc.SetMaterial(0, bmat)

tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(TARGET[0], TARGET[1], TARGET[2])
tt.Rotation = quat
tsv = ur.CoreUObject.Vector(); tsv.X = TARGET_R / 50.0; tsv.Y = TARGET_R / 50.0; tsv.Z = 0.02
tt.Scale3D = tsv
ta = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
ta.SetActorLabel("SB_Target")
tc = ta.StaticMeshComponent; tc.SetMobility(2)
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath("/Engine/BasicShapes/Cylinder.Cylinder")
tc.SetStaticMesh(cyl); tc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(5.0,0.3,0.3);\n"
    "PixelMaterialInputs.BaseColor = float3(1,0.1,0.1);\n"
    "PixelMaterialInputs.Roughness=0.3; PixelMaterialInputs.OpacityMask=1.0;",
    "SB_TargetMat", props, True)
tmat = lib.GetRuntimeMaterial("SB_TargetMat")
if tmat: tc.SetMaterial(0, tmat)

game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]; controller = pawn.GetController()
    pawn.K2_SetActorLocation(ur.CoreUObject.Vector(0, -800, SURFACE_Z + 300), False)
    cam_rot = ur.CoreUObject.Rotator(); cam_rot.Pitch = -35.0; cam_rot.Yaw = 90.0
    controller.SetControlRotation(cam_rot)

# 9. Tick + log
log_file = open(LOG_PATH, "w", encoding="utf-8")
log_file.write("frame,elapsed,bx,by,bz,dist_to_target,status\n")

def sync_ball(delta_time, elapsed_time, actors, params):
    fl = slang.ReadBackFloatBuffer("nodes")
    if fl and len(fl) > 11 * 8 + 7:
        bx, by, bz = fl[5*8+7], fl[6*8+7], fl[7*8+7]
        params["ball"].K2_SetActorLocation(ur.CoreUObject.Vector(bx, by, bz), False)
        params["frame"] = params.get("frame", 0) + 1
        dx, dy = bx - TARGET[0], by - TARGET[1]
        dist = math.sqrt(dx*dx + dy*dy)
        if params["frame"] % 10 == 0:
            params["log"].write(f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},flying\n")
        if bz <= 31.0 and not params.get("landed"):
            params["landed"] = True
            hit = dist <= TARGET_R
            st = "PASS" if hit else "FAIL"
            params["land_result"] = st
            params["log"].write(f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},LANDED_{st}\n")
            params["log"].flush()
            print(f"FIRST TOUCH: ({bx:.1f},{by:.1f},{bz:.1f}), dist={dist:.1f}, {st}")
        if params.get("landed"):
            p = params.get("pp")
            if p and abs(bx-p[0])<0.1 and abs(by-p[1])<0.1:
                params["sc"] = params.get("sc",0)+1
            else:
                params["sc"] = 0
            params["pp"] = (bx,by,bz)
            if params["sc"] > 30:
                params["log"].write(f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},settled\n")
                params["log"].write(f"\n# RESULT: {params.get('land_result','?')}\n")
                params["log"].flush(); params["log"].close()
                return False
    return True

ur.submit_tick_task("sb_sync", sync_ball, [],
    {"ball": ball, "landed": False, "frame": 0, "log": log_file}, max_duration=30.0)
print("Example scene ready.")
