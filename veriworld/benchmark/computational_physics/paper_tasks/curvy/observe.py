"""
17_surface_billiards_hard — Observation scene: large bumpy terrain + target marker + fixed start marker, no ball motion.
Shows the terrain from a fixed camera so the agent can study the landscape.
"""
import unreal_runtime as ur
import json, os, math

BASE = __PAPER_CASE_DIR__
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N    = params["grid_n"]
SPACING   = params["grid_spacing"]
BALL_R    = params["ball_radius"]
BASE_Z    = params["surface_z_base"]
GAUSSIANS = params["gaussians"]
TARGET    = params["target"]
TARGET_R  = TARGET["radius"]
START_X   = params["start"]["x"]
START_Y   = params["start"]["y"]

SHADER_PATH = os.path.join(BASE, "lean_verify", "billiard_ball.slang")

def eval_surface(x, y):
    z = BASE_Z
    for g in GAUSSIANS:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx*dx + dy*dy) / (2 * g["sigma"]**2))
    return z

world = ur.Engine.GetDefaultWorld()
slang = ur.SlangCudaPlugin.SlangCudaBlueprintLibrary

# Clean
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
assert cloth.SetShader(SHADER_PATH, "BilliardBall"), 'Observation shader failed to initialize'

# Build bumpy surface
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

    # Place ball at the fixed start point (won't move — just for reference)
    ball_z = eval_surface(START_X, START_Y) + BALL_R + 2.0
    floats[5 * 8 + 7] = START_X
    floats[6 * 8 + 7] = START_Y
    floats[7 * 8 + 7] = ball_z
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = 0.0
    floats[10 * 8 + 7] = 0.0
    floats[11 * 8 + 7] = 0.0

    slang.UploadFloatArray("nodes", floats)

# Materials
lib = ur.Engine.RuntimeMaterialLibrary
props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Masked", True)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.4, 0.55, 0.35);\n"
    "PixelMaterialInputs.Roughness = 0.7;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_SurfMat", props, True)
smat = lib.GetRuntimeMaterial("SB_SurfMat")
root = cloth.K2_GetRootComponent()
if root and smat and hasattr(root, 'SetMaterial'):
    root.SetMaterial(0, smat)

# Target marker
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")
target_z = eval_surface(TARGET["x"], TARGET["y"]) + 1.0
tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(TARGET["x"], TARGET["y"], target_z)
tt.Rotation = quat
tsv = ur.CoreUObject.Vector(); tsv.X = TARGET_R / 30.0; tsv.Y = TARGET_R / 30.0; tsv.Z = 0.02
tt.Scale3D = tsv
ta = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
ta.SetActorLabel("SB_Target")
tc = ta.StaticMeshComponent; tc.SetMobility(2)
tc.SetStaticMesh(cyl); tc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(5.0, 0.3, 0.3);\n"
    "PixelMaterialInputs.BaseColor = float3(1.0, 0.1, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.3;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_TargetMat", props, True)
tmat = lib.GetRuntimeMaterial("SB_TargetMat")
if tmat: tc.SetMaterial(0, tmat)

# Arrow above target
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

cone = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath("/Engine/BasicShapes/Cone.Cone")
head_t = ur.CoreUObject.Transform()
head_t.Translation = ur.CoreUObject.Vector(TARGET["x"], TARGET["y"], arrow_base_z + 10.0)
head_rot = ur.CoreUObject.Rotator(); head_rot.Roll = 180.0
head_t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(head_rot)
head_sv = ur.CoreUObject.Vector(); head_sv.X = 0.25; head_sv.Y = 0.25; head_sv.Z = 0.3
head_t.Scale3D = head_sv
arrow_head = world.SpawnActorEx(ur.Engine.StaticMeshActor, head_t, 1)
arrow_head.SetActorLabel("SB_ArrowHead")
head_c = arrow_head.StaticMeshComponent; head_c.SetMobility(2)
head_c.SetStaticMesh(cone); head_c.SetCollisionEnabled(0)
if tmat: head_c.SetMaterial(0, tmat)

# Single green sphere at the fixed start point
sphere_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
start_z = eval_surface(START_X, START_Y) + BALL_R + 5.0
mt = ur.CoreUObject.Transform()
mt.Translation = ur.CoreUObject.Vector(START_X, START_Y, start_z)
mt.Rotation = quat
msv = ur.CoreUObject.Vector(); msv.X = 0.3; msv.Y = 0.3; msv.Z = 0.3
mt.Scale3D = msv
marker = world.SpawnActorEx(ur.Engine.StaticMeshActor, mt, 1)
marker.SetActorLabel("SB_StartMarker")
mc = marker.StaticMeshComponent; mc.SetMobility(2)
mc.SetStaticMesh(sphere_mesh); mc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.EmissiveColor = float3(0.5, 6.0, 0.5);\n"
    "PixelMaterialInputs.BaseColor = float3(0.1, 0.95, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.2;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_StartMat", props, True)
zmat = lib.GetRuntimeMaterial("SB_StartMat")
if zmat: mc.SetMaterial(0, zmat)

# Camera — further back for larger map
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

print("[DONE] Observation scene ready — large bumpy terrain + crater target + fixed start marker.")
