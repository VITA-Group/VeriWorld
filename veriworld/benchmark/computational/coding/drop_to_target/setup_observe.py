"""
Round 0 observe: flat surface + ball + target circle. No deformation.
Reads params.json for positions.
"""
import unreal_runtime as ur
import json
import os

BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/unreal_projects_lean/lean/unit_tests/12c_drop_to_target"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N = params["grid_n"]
SPACING = params["grid_spacing"]
BALL_R = params["ball_radius"]
SURFACE_Z = params["surface_z"]
BALL_START = params["ball_start"]
TARGET = params["target"]
TARGET_R = params["target_radius"]
SHADER_PATH = BASE + "/lean_verify/slide_ball.slang"

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

# 4. Flat surface + ball
floats = slang.ReadBackFloatBuffer("nodes")
expected = GRID_N * GRID_N * 8
if floats and len(floats) >= expected:
    half_w = (GRID_N - 1) / 2.0
    for gi in range(GRID_N):
        for gj in range(GRID_N):
            idx = gi * GRID_N + gj
            base = idx * 8
            floats[base + 0] = (gi - GRID_N / 2.0) * SPACING
            floats[base + 1] = (gj - GRID_N / 2.0) * SPACING
            floats[base + 2] = SURFACE_Z
            floats[base + 3] = 0.0

    # Ball at center
    floats[5 * 8 + 7] = BALL_START[0]
    floats[6 * 8 + 7] = BALL_START[1]
    floats[7 * 8 + 7] = BALL_START[2]
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = 0.0
    floats[10 * 8 + 7] = 0.0
    floats[11 * 8 + 7] = 0.0
    slang.UploadFloatArray("nodes", floats)
    print("Flat surface + ball uploaded")

# 5. Surface material
lib = ur.Engine.RuntimeMaterialLibrary
props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Masked", True)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.3, 0.7, 0.4);\n"
    "PixelMaterialInputs.Roughness = 0.5;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_SlideMat", props, True)
mat = lib.GetRuntimeMaterial("SB_SlideMat")
root = slide.K2_GetRootComponent()
if root and mat and hasattr(root, 'SetMaterial'):
    root.SetMaterial(0, mat)

# 6. Visual ball
bt = ur.CoreUObject.Transform()
bt.Translation = ur.CoreUObject.Vector(BALL_START[0], BALL_START[1], BALL_START[2])
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

# 7. Target circle (red disc on ground)
tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(TARGET[0], TARGET[1], TARGET[2])
tt.Rotation = quat
tsv = ur.CoreUObject.Vector()
tsv.X = TARGET_R / 50.0
tsv.Y = TARGET_R / 50.0
tsv.Z = 0.02
tt.Scale3D = tsv
target_actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
target_actor.SetActorLabel("SB_Target")
tc = target_actor.StaticMeshComponent
tc.SetMobility(2)
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath("/Engine/BasicShapes/Cylinder.Cylinder")
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

# 8. Camera — see both surface and ground target
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]
    controller = pawn.GetController()
    pawn.K2_SetActorLocation(ur.CoreUObject.Vector(0, -800, SURFACE_Z + 300), False)
    cam_rot = ur.CoreUObject.Rotator(); cam_rot.Pitch = -35.0; cam_rot.Yaw = 90.0
    controller.SetControlRotation(cam_rot)

print("[DONE] Observe setup complete")
