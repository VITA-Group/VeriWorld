"""Round-0 observe scene for SurfaceBilliards.

Builds the gaussian terrain + A launch marker + B target crater marker.
No ball motion — Round 0 is the agent's camera-flyover observation of
the scene geometry. Based on ``unit_test_demos/bounce_shot_ab.py``;
same scale (60×60 grid, ≤9 gaussians) — proven to run on
PackagedOutput_dev.
"""
import json
import math
import os

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

SHADER_PATH = os.path.join(BASE, "lean_verify", "bouncy_ball.slang")


def eval_surface(x, y):
    z = BASE_Z
    for g in GAUSSIANS:
        dx, dy = x - g["cx"], y - g["cy"]
        z += g["height"] * math.exp(-(dx * dx + dy * dy) / (2 * g["sigma"] ** 2))
    return z


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

# ── Spawn cloth surface ──
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

# Basic surface material
mat = ur.StaticLoadObject(
    ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
root = cloth.K2_GetRootComponent()
if root and mat:
    root.SetMaterial(0, mat)

# ── Build terrain; park a static ball at A for visualisation ──
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
    # Ball parked at A with zero velocity (visible, not simulated)
    floats[5 * 8 + 7] = A["x"]
    floats[6 * 8 + 7] = A["y"]
    floats[7 * 8 + 7] = A["z"]
    floats[8 * 8 + 7] = BALL_R
    floats[9 * 8 + 7] = 0.0
    floats[10 * 8 + 7] = 0.0
    floats[11 * 8 + 7] = 0.0
    slang.UploadFloatArray("nodes", floats)

# ── Markers ──
sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")
label_rot = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, -90, 0))

# A marker — green sphere at launch point
mt = ur.CoreUObject.Transform()
mt.Translation = ur.CoreUObject.Vector(A["x"], A["y"], A["z"] + 20)
mt.Rotation = quat
msv = ur.CoreUObject.Vector(); msv.X = msv.Y = msv.Z = 0.2
mt.Scale3D = msv
marker_a = world.SpawnActorEx(ur.Engine.StaticMeshActor, mt, 1)
marker_a.SetActorLabel("SB_MarkerA")
mc = marker_a.StaticMeshComponent
mc.SetMobility(2)
mc.SetStaticMesh(sphere)
mc.SetCollisionEnabled(0)

# A label
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

# B target — red disc at target crater
target_z = eval_surface(B["x"], B["y"]) + 1.0
tt = ur.CoreUObject.Transform()
tt.Translation = ur.CoreUObject.Vector(B["x"], B["y"], target_z)
tt.Rotation = quat
tsv = ur.CoreUObject.Vector()
tsv.X = TARGET_R / 30.0
tsv.Y = TARGET_R / 30.0
tsv.Z = 0.02
tt.Scale3D = tsv
target_actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, tt, 1)
target_actor.SetActorLabel("SB_Target")
tc = target_actor.StaticMeshComponent
tc.SetMobility(2)
tc.SetStaticMesh(cyl)
tc.SetCollisionEnabled(0)

# B label
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

# ── Camera: side view to see both A and B ──
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

print(f"[OBSERVE] Terrain + A launch marker + B target built.")
print(f"  A = ({A['x']}, {A['y']}, {A['z']})  (launch, above surface)")
print(f"  B = ({B['x']}, {B['y']}), R = {TARGET_R}")
print(f"  Grid: {GRID_N}x{GRID_N}, {len(GAUSSIANS)} gaussians")
