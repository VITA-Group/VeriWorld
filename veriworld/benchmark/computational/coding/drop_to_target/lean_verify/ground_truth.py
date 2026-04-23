"""
Lean-verified ground truth solution for 12c_drop_to_target.

This script mirrors the Lean computation EXACTLY:
  1. Read params.json
  2. Compute tilt angle θ via binary search (same as Lean findTiltAngle)
  3. Tilt the surface toward the target by θ
  4. Set up scene in UE5: surface + ball + target marker + camera + tick

If the Lean proof compiled, this MUST produce a ball-in-target animation.
If it doesn't, either the physics model doesn't match the shader or the
params.json wasn't Lean-verified.

Run:
  cd C:/Users/yanzh/projects/unreal_projects/demo2/Plugins/UELivePy/Tests
  python utils/test_runner.py "<this_file_absolute_path>"
"""
import unreal_runtime as ur
import json
import os
import math

# ============================================================
# 1. Read params
# ============================================================

BASE = "C:/Users/yanzh/projects/AxisWorld-benchmark/unreal_projects_lean/lean/unit_tests/12c_drop_to_target"
with open(os.path.join(BASE, "params.json"), "r") as f:
    params = json.load(f)

GRID_N     = params["grid_n"]
SPACING    = params["grid_spacing"]
BALL_R     = params["ball_radius"]
SURFACE_Z  = params["surface_z"]
BALL_START = params["ball_start"]
TARGET     = params["target"]
TARGET_R   = params["target_radius"]
SHADER_PATH = BASE + "/agent_interface/slide_ball.slang"

GRAVITY  = 300.0   # must match slide_ball.slang
FRICTION = 0.4     # must match slide_ball.slang
GRID_HALF_W = (GRID_N / 2.0) * SPACING  # 240.0

# ============================================================
# 2. Mirror Lean physics model — compute tilt angle θ
# ============================================================

# Target distance and direction (same as Lean targetDist/targetDirX/targetDirY)
target_dist = math.sqrt(TARGET[0]**2 + TARGET[1]**2)
if target_dist > 0.001:
    dir_x = TARGET[0] / target_dist
    dir_y = TARGET[1] / target_dist
else:
    dir_x, dir_y = 1.0, 0.0

RAMP_LEN = 120.0  # half the grid — ramp only in target direction

def landing_distance(theta):
    """Exact mirror of Lean landingDistance (ramp model, shader friction)."""
    sinT = math.sin(theta)
    cosT = math.cos(theta)
    # CUDA shader friction: constant decel μ·g. Threshold: sinθ > μ.
    if sinT <= FRICTION:
        return 0.0
    a_net = GRAVITY * (sinT - FRICTION)
    surf_dist = RAMP_LEN / cosT  # distance along the ramp surface
    v_exit = math.sqrt(2.0 * a_net * surf_dist)
    h_exit = SURFACE_Z - RAMP_LEN * math.tan(theta)
    if h_exit <= 0:
        return 0.0
    v_h = v_exit * cosT
    v_z = -v_exit * sinT
    disc = v_z**2 + 2.0 * GRAVITY * h_exit
    if disc < 0:
        return 0.0
    t_flight = (-v_z + math.sqrt(disc)) / GRAVITY
    d_total = RAMP_LEN + v_h * t_flight
    return d_total

def find_tilt_angle(target_d):
    """Exact mirror of Lean findTiltAngle — 50-iteration binary search."""
    lo = 0.05
    hi = 0.75
    for _ in range(50):
        mid = (lo + hi) / 2.0
        d = landing_distance(mid)
        if d < target_d:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0

theta = find_tilt_angle(target_dist)
landing_d = landing_distance(theta)
landing_x = landing_d * dir_x
landing_y = landing_d * dir_y

print("=" * 60)
print("LEAN-VERIFIED GROUND TRUTH SOLUTION")
print("=" * 60)
print(f"  Target:    ({TARGET[0]:.1f}, {TARGET[1]:.1f}), radius={TARGET_R:.1f}")
print(f"  Target distance: {target_dist:.1f} cm")
print(f"  Direction: ({dir_x:.4f}, {dir_y:.4f})")
print(f"  Tilt angle: {theta:.4f} rad = {math.degrees(theta):.2f} deg")
print(f"  Landing distance: {landing_d:.1f} cm")
print(f"  Expected landing: ({landing_x:.1f}, {landing_y:.1f})")
print(f"  Error: {abs(landing_d - target_dist):.4f} cm")
print("=" * 60)

# ============================================================
# 3. Set up UE5 scene
# ============================================================

world = ur.Engine.GetDefaultWorld()
slang = ur.SlangCudaPlugin.SlangCudaBlueprintLibrary

# Clean
if hasattr(ur.ChaosHelper, 'GPUClothActor'):
    for a in world.GetActorsOfClass(ur.ChaosHelper.GPUClothActor):
        a.K2_DestroyActor()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    if a.GetActorLabel().startswith("SB_"):
        a.K2_DestroyActor()

# Spawn GPUClothActor
ct = ur.CoreUObject.Transform()
ct.Translation = ur.CoreUObject.Vector(0, 0, 0)
quat = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(0, 0, 0))
ct.Rotation = quat
sv = ur.CoreUObject.Vector(); sv.X = sv.Y = sv.Z = 1.0
ct.Scale3D = sv
slide = world.SpawnActorEx(ur.ChaosHelper.GPUClothActor, ct, 1)
slide.SetActorLabel("SB_Slide")

# InitCloth + SetShader
slide.InitCloth(GRID_N, SPACING, 800.0, 8.0, 3)
ok = slide.SetShader(SHADER_PATH, "SlideBall")
print(f"SetShader: {ok}")

# ============================================================
# 4. Compute tilted surface — the Lean-verified deformation
# ============================================================

floats = slang.ReadBackFloatBuffer("nodes")
expected = GRID_N * GRID_N * 8

if floats and len(floats) >= expected:
    # Ramp model: slope only in the target direction, for the first RAMP_LEN cm.
    # Beyond the ramp, the surface drops sharply so the ball launches off.
    tan_theta = math.tan(theta)

    for gi in range(GRID_N):
        for gj in range(GRID_N):
            idx = gi * GRID_N + gj
            base = idx * 8

            x = (gi - GRID_N / 2.0) * SPACING
            y = (gj - GRID_N / 2.0) * SPACING

            # Projection of (x, y) onto target direction
            proj = x * dir_x + y * dir_y

            # Ramp: slope only for positive projection (toward target), up to RAMP_LEN
            if proj <= 0:
                # Behind the ball: flat at surface height (wall to prevent backward roll)
                z = SURFACE_Z + 20.0
            elif proj <= RAMP_LEN:
                # Ramp zone: slopes downward toward target
                z = SURFACE_Z - tan_theta * proj
            else:
                # Beyond ramp: sharp drop-off (ball launches into free fall)
                z = SURFACE_Z - tan_theta * RAMP_LEN - (proj - RAMP_LEN) * 3.0

            floats[base + 0] = x
            floats[base + 1] = y
            floats[base + 2] = z
            floats[base + 3] = 0.0  # invMass=0 (static surface)

    # Ball initial state: center of surface, just above
    floats[5 * 8 + 7] = BALL_START[0]       # ball X
    floats[6 * 8 + 7] = BALL_START[1]       # ball Y
    floats[7 * 8 + 7] = SURFACE_Z + BALL_R + 10.0  # ball Z (center is flat)
    floats[8 * 8 + 7] = BALL_R              # ball radius
    floats[9 * 8 + 7] = 0.0                 # ball VX
    floats[10 * 8 + 7] = 0.0                # ball VY
    floats[11 * 8 + 7] = 0.0                # ball VZ

    slang.UploadFloatArray("nodes", floats)
    print(f"Tilted surface uploaded (θ={math.degrees(theta):.1f}°, tan={tan_theta:.4f})")

# ============================================================
# 5. Materials
# ============================================================

lib = ur.Engine.RuntimeMaterialLibrary
props = lib.MakeSurfaceMaterialProperties("DefaultLit", "Masked", True)

# Surface — green
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.3, 0.7, 0.4);\n"
    "PixelMaterialInputs.Roughness = 0.5;\n"
    "PixelMaterialInputs.OpacityMask = 1.0;",
    "SB_SlideMat", props, True)
mat = lib.GetRuntimeMaterial("SB_SlideMat")
root = slide.K2_GetRootComponent()
if root and mat and hasattr(root, 'SetMaterial'):
    root.SetMaterial(0, mat)

# ============================================================
# 6. Visual ball
# ============================================================

bt = ur.CoreUObject.Transform()
bt.Translation = ur.CoreUObject.Vector(
    BALL_START[0], BALL_START[1], SURFACE_Z + BALL_R + 10.0)
bt.Rotation = quat
bsv = ur.CoreUObject.Vector(); bsv.X = bsv.Y = bsv.Z = BALL_R / 50.0
bt.Scale3D = bsv
ball = world.SpawnActorEx(ur.Engine.StaticMeshActor, bt, 1)
ball.SetActorLabel("SB_Ball")
bc = ball.StaticMeshComponent
bc.SetMobility(2)
sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
bc.SetStaticMesh(sphere)
bc.SetCollisionEnabled(0)
lib.CreateMaterialFromCodeAndWait(
    "PixelMaterialInputs.BaseColor = float3(0.9, 0.2, 0.1);\n"
    "PixelMaterialInputs.Roughness = 0.15;",
    "SB_BallMat", props, True)
bmat = lib.GetRuntimeMaterial("SB_BallMat")
if bmat: bc.SetMaterial(0, bmat)

# ============================================================
# 7. Target circle marker (red disc on ground)
# ============================================================

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
cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")
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

# ============================================================
# 8. Camera — see both surface and ground target
# ============================================================

game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns and len(pawns) > 0:
    pawn = pawns[0]
    controller = pawn.GetController()
    pawn.K2_SetActorLocation(
        ur.CoreUObject.Vector(0, -800, SURFACE_Z + 300), False)
    cam_rot = ur.CoreUObject.Rotator()
    cam_rot.Pitch = -35.0
    cam_rot.Yaw = 90.0
    controller.SetControlRotation(cam_rot)

# ============================================================
# 9. Ball sync tick + verify log
# ============================================================

LOG_PATH = os.path.join(BASE, "lean_verify", "log_for_verify.txt")
log_file = open(LOG_PATH, "w", encoding="utf-8")
log_file.write("frame,elapsed,bx,by,bz,dist_to_target,status\n")

def sync_ball(delta_time, elapsed_time, actors, params):
    fl = slang.ReadBackFloatBuffer("nodes")
    if fl and len(fl) > 11 * 8 + 7:
        bx = fl[5 * 8 + 7]
        by = fl[6 * 8 + 7]
        bz = fl[7 * 8 + 7]
        params["ball"].K2_SetActorLocation(
            ur.CoreUObject.Vector(bx, by, bz), False)

        params["frame"] = params.get("frame", 0) + 1
        dx = bx - TARGET[0]
        dy = by - TARGET[1]
        dist = math.sqrt(dx*dx + dy*dy)

        # Log every 10th frame to keep file small
        if params["frame"] % 10 == 0:
            params["log"].write(
                f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},flying\n")

        # Detect first landing (ball on ground: groundZ + ballR = 15 + 15 = 30)
        if bz <= 31.0 and not params.get("landed"):
            params["landed"] = True
            hit = dist <= TARGET_R
            status = "PASS" if hit else "FAIL"

            params["land_frame"] = params["frame"]
            params["land_result"] = status

            # Mark first-touch in log
            params["log"].write(
                f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},LANDED_{status}\n")
            params["log"].flush()

            print(f"FIRST TOUCH: ({bx:.1f}, {by:.1f}, {bz:.1f})")
            print(f"  Distance to target: {dist:.1f} cm")
            print(f"  Target radius: {TARGET_R:.1f} cm")
            print(f"  RESULT: {status}")
            # Don't stop — keep recording full trajectory

        # After landing, detect settled (velocity ~ 0) to write final summary
        if params.get("landed") and not params.get("summarized"):
            prev = params.get("prev_pos")
            if prev and abs(bx - prev[0]) < 0.1 and abs(by - prev[1]) < 0.1:
                params["settled_count"] = params.get("settled_count", 0) + 1
            else:
                params["settled_count"] = 0
            params["prev_pos"] = (bx, by, bz)

            if params["settled_count"] > 30:  # settled for 30 frames
                params["summarized"] = True
                params["log"].write(
                    f"{params['frame']},{elapsed_time:.3f},{bx:.1f},{by:.1f},{bz:.1f},{dist:.1f},settled\n")
                params["log"].write(f"\n# VERIFY RESULT (first touch)\n")
                params["log"].write(f"# first_touch_frame: {params.get('land_frame', '?')}\n")
                params["log"].write(f"# RESULT: {params.get('land_result', '?')}\n")
                params["log"].flush()
                params["log"].close()
                print(f"Ball settled. Log closed.")
                return False  # stop tick task

    return True

ur.submit_tick_task("sb_sync", sync_ball, [],
                    {"ball": ball, "landed": False, "frame": 0, "log": log_file},
                    max_duration=60.0)

print(f"\n[DONE] Ground truth scene ready. Logging to: {LOG_PATH}")
