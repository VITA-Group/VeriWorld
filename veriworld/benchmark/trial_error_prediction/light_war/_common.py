"""lightwar — shared helpers (parabola math, frame stacking, UE snippets)."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import shutil
import zlib
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from veriworld.infra.interactive.engine import InteractiveEngine

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
GROUNDTRUTH_PATH = HERE / "groundtruth.json"

# v2 cooked build saves screenshots under demo1/Saved/Screenshots/Windows.
# Override with `LIGHTWAR_SCREENSHOT_DIR` env var if you launch a different
# build path.


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Parabola math
# ---------------------------------------------------------------------------

def parabola_xyz(launch_xyz: tuple, yaw_deg: float, pitch_deg: float,
                 v0_cm_s: float, t: float,
                 gravity_cm_s2: float = 980.0) -> tuple:
    """Closed-form parabolic position at time ``t``.
    Returns (x, y, z, vx, vy, vz)."""
    yr = math.radians(yaw_deg); pr = math.radians(pitch_deg)
    vx0 = v0_cm_s * math.cos(pr) * math.cos(yr)
    vy0 = v0_cm_s * math.cos(pr) * math.sin(yr)
    vz0 = v0_cm_s * math.sin(pr)
    lx, ly, lz = launch_xyz
    g = float(gravity_cm_s2)
    x = lx + vx0 * t
    y = ly + vy0 * t
    z = lz + vz0 * t - 0.5 * g * t * t
    return (x, y, z, vx0, vy0, vz0 - g * t)


async def find_landing(engine, launch_xyz: tuple, yaw_deg: float,
                       pitch_deg: float, v0_cm_s: float,
                       *, ground_raycast_code,
                       parse_ground_z,
                       unwrap,
                       gravity_cm_s2: float = 980.0,
                       dt: float = 0.10, max_t: float = 12.0) -> dict:
    """Phase A — walk the analytic parabola in ``dt`` steps, raycast at
    each step (no debug visualization), stop on the first step where
    the projectile is at-or-below the terrain. Returns:
      ``landing``: dict with X/Y/Z/t (or None if off-map)
      ``samples``: list of dicts {t, x, y, z, ground_z} for every step
                   walked (useful for diagnostics + later snapshotting)
      ``off_map``: True if no ground was ever hit (e.g. flew past the
                   landscape, or pitch ≤ 0)
    """
    landing = None
    samples = []
    n = int(max_t / dt) + 1
    for i in range(n):
        t = i * dt
        x, y, z, _, _, _ = parabola_xyz(
            launch_xyz, yaw_deg, pitch_deg, v0_cm_s, t,
            gravity_cm_s2=gravity_cm_s2)
        gz_here = parse_ground_z(unwrap(
            await engine.python_exec(ground_raycast_code(x, y))))
        samples.append({"t": t, "x": x, "y": y, "z": z, "ground_z": gz_here})
        if i > 1 and gz_here is not None and z <= gz_here:
            landing = {"X": x, "Y": y, "Z": gz_here, "t": t}
            break
    return {"landing": landing, "samples": samples,
            "off_map": landing is None}


def evenly_spaced_waypoints(launch_xyz: tuple, yaw_deg: float,
                             pitch_deg: float, v0_cm_s: float,
                             t_land: float, n_frames: int,
                             gravity_cm_s2: float = 980.0) -> list:
    """Phase B helper — pick ``n_frames`` evenly-spaced (t, x, y, z,
    vx, vy, vz) tuples covering ``(0, t_land)`` so the screenshot
    sequence shows the entire arc."""
    waypoints = []
    for i in range(n_frames):
        f = (i + 0.5) / n_frames                  # avoid t=0 and t=t_land exactly
        t = t_land * f
        x, y, z, vx, vy, vz = parabola_xyz(
            launch_xyz, yaw_deg, pitch_deg, v0_cm_s, t,
            gravity_cm_s2=gravity_cm_s2)
        waypoints.append((t, x, y, z, vx, vy, vz))
    return waypoints


def velocity_to_yaw_pitch(vx: float, vy: float, vz: float) -> tuple:
    """Convert a velocity vector into (yaw_deg, pitch_deg) in UE convention.
    Yaw is measured from +X toward +Y (atan2(vy, vx)), pitch is the angle
    above the horizontal plane (atan2(vz, |horizontal|))."""
    yaw = math.degrees(math.atan2(vy, vx))
    horiz = math.hypot(vx, vy)
    pitch = math.degrees(math.atan2(vz, horiz))
    return yaw, pitch


# ---------------------------------------------------------------------------
# Frame stacking
# ---------------------------------------------------------------------------

def stack_frames(
    frames: list,
    out_path: Path,
    *,
    cols: int = 3,
    rows: int = 2,
    labels: Optional[list] = None,
    bg_color=(20, 20, 20),
    label_color=(255, 255, 255),
) -> Path:
    """Stitch frames into a single rows×cols composite, with t labels.

    `frames` is a list of file paths or PIL.Image instances (length =
    cols×rows). `labels` is a parallel list of strings drawn at the
    top-left of each cell. Returns ``out_path``.
    """
    assert len(frames) == cols * rows, (
        f"need exactly {cols*rows} frames, got {len(frames)}")
    imgs = []
    for f in frames:
        if isinstance(f, (str, Path)):
            imgs.append(Image.open(str(f)).convert("RGB"))
        else:
            imgs.append(f.convert("RGB"))
    fw, fh = imgs[0].size
    composite = Image.new("RGB", (fw * cols, fh * rows), bg_color)
    draw = ImageDraw.Draw(composite)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    for k, im in enumerate(imgs):
        if im.size != (fw, fh):
            im = im.resize((fw, fh))
        r, c = divmod(k, cols)
        composite.paste(im, (c * fw, r * fh))
        if labels and k < len(labels):
            txt = labels[k]
            draw.rectangle((c * fw + 4, r * fh + 4, c * fw + 4 + 9*len(txt) + 12,
                            r * fh + 32), fill=(0, 0, 0))
            draw.text((c * fw + 10, r * fh + 8), txt,
                      fill=label_color, font=font)
    composite.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# UE-side python_exec snippets
# ---------------------------------------------------------------------------

OPEN_FOREST_CODE = (
    # ``OpenLevel`` invalidates the running script's world handle, so any
    # ``print`` placed AFTER the OpenLevel call may never reach the WS.
    # We print the status BEFORE firing OpenLevel — caller waits on that
    # marker, then re-runs anything that needs the new world.
    'import unreal_runtime as ur\n'
    'cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
    'print(f"CUR_LEVEL {cur}")\n'
    'if cur == "Showcase":\n'
    '    print("LEVEL_READY")\n'
    'else:\n'
    '    print("LEVEL_LOADING")\n'
    '    ur.Engine.GameplayStatics.OpenLevel('
    '"/Game/MAEBeechForest/Maps/Showcase.Showcase", True, "")\n'
)


def ground_z_at_code(x: float, y: float,
                     sky_z: float = 30000.0,
                     floor_z: float = -8000.0) -> str:
    """Raycast straight down at (x, y) using ``KismetSystemLibrary``'s
    ``LineTraceSingle`` (the API contract the cooked v2 build exposes —
    plain ``Engine.LineTraceSingle`` is NOT available there). Print
    ``GROUND_Z value`` on a hit, ``GROUND_Z None`` on miss."""
    # Pass 0 (== EDrawDebugTrace::None) for the draw-debug arg so we
    # don't spam 600+ red trace lines during ground-z cache warmup.
    # UE's KismetSystemLibrary accepts int values for enum arguments.
    return f"""
import unreal_runtime as ur
ksm = ur.Engine.KismetSystemLibrary
s = ur.CoreUObject.Vector(); s.X={x}; s.Y={y}; s.Z={sky_z}
e = ur.CoreUObject.Vector(); e.X={x}; e.Y={y}; e.Z={floor_z}
hit = ur.Engine.HitResult()
tc = ur.CoreUObject.LinearColor(); tc.R=1; tc.G=0; tc.B=0; tc.A=1
hc = ur.CoreUObject.LinearColor(); hc.R=0; hc.G=1; hc.B=0; hc.A=1
got = ksm.LineTraceSingle(s, e, ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
    False, [], 0, hit, True, tc, hc, 0.0)
if got and hit.bBlockingHit:
    print(f'GROUND_Z {{float(hit.Location.Z):.2f}}')
else:
    print('GROUND_Z None')
"""


def parse_ground_z(out: str) -> Optional[float]:
    for line in out.splitlines():
        if line.startswith("GROUND_Z"):
            tok = line.split(maxsplit=1)[1]
            return None if tok == "None" else float(tok)
    return None


def spawn_beacon_code(beacon: dict, ground_z: float) -> str:
    """Spawn (or refresh) a 'lighthouse' tower + PointLight at beacon XY.
    Idempotent — wipes any prior ``_LightwarBeacon_*`` actors first.
    Mirrors the proven beacon pattern from
    ``VeriWorld-benchmark/.../lighting/spawn_beacon.py`` (uses
    ``RuntimeglTFLibrary.LoadStaticMeshFromPath``,
    ``StaticLoadObject``, ``SpawnActorEx`` with a Transform — those are
    the runtime APIs that survive cooking with ``WITH_EDITOR=0``)."""
    bx, by = beacon["X"], beacon["Y"]
    th = beacon["tower_height_cm"]
    tr = beacon["tower_radius_cm"]
    gap = beacon["light_gap_cm"]
    tc = beacon["tower_color"]
    lc = beacon["light_color"]
    intensity = beacon["light_intensity"]
    atten = beacon["light_attenuation_cm"]
    src_r = beacon["light_source_radius"]
    vol = beacon["vol_scatter"]
    return f"""
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()

# Wipe previous beacon actors.
for cls in (ur.Engine.PointLight, ur.Engine.StaticMeshActor):
    for a in world.GetActorsOfClass(cls):
        try:
            n = a.GetActorLabel()
            if n.startswith("_LightwarBeacon_"):
                a.K2_DestroyActor()
        except Exception: pass

cyl = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cylinder.Cylinder")
base_mat = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
if cyl is None or base_mat is None:
    print("BEACON_ERROR missing_cylinder_or_material")
else:
    base_z = {ground_z}
    tower_h = {th}
    cyl_center_z = base_z + tower_h * 0.5

    t = ur.CoreUObject.Transform()
    tv = ur.CoreUObject.Vector(); tv.X={bx}; tv.Y={by}; tv.Z=cyl_center_z
    t.Translation = tv
    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(0, 0, 0))
    sv = ur.CoreUObject.Vector()
    sv.X = {tr}/50.0; sv.Y = {tr}/50.0; sv.Z = tower_h/100.0
    t.Scale3D = sv

    tower = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    tower.SetActorLabel("_LightwarBeacon_Tower")
    tcomp = tower.StaticMeshComponent
    tcomp.SetMobility(2)
    tcomp.SetStaticMesh(cyl)
    tcomp.SetMaterial(0, base_mat)
    mid = tcomp.CreateDynamicMaterialInstance(0)
    if mid is not None:
        col = ur.CoreUObject.LinearColor()
        col.R = {tc[0]}; col.G = {tc[1]}; col.B = {tc[2]}; col.A = 1.0
        mid.SetVectorParameterValue("Color", col)
    tcomp.SetCollisionEnabled(0)
    tcomp.SetSimulatePhysics(False)

    # Light atop the tower.
    light_z = base_z + tower_h + {gap}
    lt = ur.CoreUObject.Transform()
    ltv = ur.CoreUObject.Vector(); ltv.X={bx}; ltv.Y={by}; ltv.Z=light_z
    lt.Translation = ltv
    lt.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(-90.0, 0, 0))

    light = world.SpawnActorEx(ur.Engine.PointLight, lt, 1)
    light.SetActorLabel("_LightwarBeacon_Light")
    lcomp = light.LightComponent
    lcomp.SetMobility(2)
    lcomp.SetIntensity({intensity})
    lcol = ur.CoreUObject.LinearColor()
    lcol.R = {lc[0]}; lcol.G = {lc[1]}; lcol.B = {lc[2]}; lcol.A = 1.0
    lcomp.SetLightColor(lcol)
    if hasattr(lcomp, "SetAttenuationRadius"):
        lcomp.SetAttenuationRadius({atten})
    if hasattr(lcomp, "SetSourceRadius"):
        lcomp.SetSourceRadius({src_r})
    if hasattr(lcomp, "SetVolumetricScatteringIntensity"):
        lcomp.SetVolumetricScatteringIntensity({vol})
    print(f"BEACON_OK base_z={{base_z:.1f}} top_z={{base_z + tower_h:.1f}} light_z={{light_z:.1f}}")
"""


def reset_pawn_pose_code(x: float, y: float, z: float,
                         pitch: float, yaw: float, roll: float = 0.0) -> str:
    return f"""
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
gm = ur.Engine.GetGameMode()
pawn = world.GetActorsOfClass(gm.DefaultPawnClass)[0]
ctrl = pawn.GetController()
v = ur.CoreUObject.Vector(); v.X={x}; v.Y={y}; v.Z={z}
pawn.K2_SetActorLocation(v, False)
rot = ur.CoreUObject.Rotator(); rot.Pitch={pitch}; rot.Yaw={yaw}; rot.Roll={roll}
ctrl.SetControlRotation(rot)
print(f"PAWN_AT {x:.1f} {y:.1f} {z:.1f} pitch={pitch:.2f} yaw={yaw:.2f}")
"""


def spawn_projectile_marker_code(x: float, y: float, z: float,
                                 *, radius_cm: float = 1500.0,
                                 color=(1.0, 0.25, 0.05),
                                 light_intensity: float = 8_000_000.0,
                                 light_radius_cm: float = 40000.0) -> str:
    """Spawn (or move) a brightly-tinted spherical projectile marker at
    (x, y, z). Idempotent — reuses the actor labeled
    ``_LightwarProjectile`` if it already exists, otherwise spawns one
    new. Mirrors the proven sphere-spawn pattern from
    ``_parabola_artillery_v2`` (RuntimeglTFLibrary mesh load +
    ``SpawnActorEx`` with a Transform + ``CreateDynamicMaterialInstance``)."""
    cr, cg, cb = color
    return f"""
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()

# Find or spawn the projectile sphere AND a paired tracer PointLight.
proj = None
proj_light = None
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        if a.GetActorLabel() == "_LightwarProjectile":
            proj = a; break
    except Exception: pass
for a in world.GetActorsOfClass(ur.Engine.PointLight):
    try:
        if a.GetActorLabel() == "_LightwarProjectileLight":
            proj_light = a; break
    except Exception: pass

px, py, pz = {x}, {y}, {z}

if proj is None:
    sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
        "/Engine/BasicShapes/Sphere.Sphere")
    base_mat = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,
        "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
    if sphere is None or base_mat is None:
        print("PROJ_ERROR missing_sphere_or_material")
    else:
        t = ur.CoreUObject.Transform()
        tv = ur.CoreUObject.Vector(); tv.X=px; tv.Y=py; tv.Z=pz
        t.Translation = tv
        t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
            ur.CoreUObject.Rotator(0,0,0))
        sv = ur.CoreUObject.Vector()
        # Engine sphere mesh has radius 50 cm at scale 1.
        sv.X = {radius_cm}/50.0; sv.Y = {radius_cm}/50.0; sv.Z = {radius_cm}/50.0
        t.Scale3D = sv
        proj = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
        proj.SetActorLabel("_LightwarProjectile")
        comp = proj.StaticMeshComponent
        comp.SetMobility(2)
        comp.SetStaticMesh(sphere)
        comp.SetMaterial(0, base_mat)
        mid = comp.CreateDynamicMaterialInstance(0)
        if mid is not None:
            col = ur.CoreUObject.LinearColor()
            col.R={cr}; col.G={cg}; col.B={cb}; col.A=1.0
            mid.SetVectorParameterValue("Color", col)
        comp.SetCollisionEnabled(0)
        comp.SetSimulatePhysics(False)

if proj_light is None:
    lt = ur.CoreUObject.Transform()
    ltv = ur.CoreUObject.Vector(); ltv.X=px; ltv.Y=py; ltv.Z=pz
    lt.Translation = ltv
    lt.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(-90.0, 0, 0))
    proj_light = world.SpawnActorEx(ur.Engine.PointLight, lt, 1)
    proj_light.SetActorLabel("_LightwarProjectileLight")
    plc = proj_light.LightComponent
    plc.SetMobility(2)
    plc.SetIntensity({light_intensity})
    lcol = ur.CoreUObject.LinearColor()
    lcol.R={cr}; lcol.G={cg}; lcol.B={cb}; lcol.A=1.0
    plc.SetLightColor(lcol)
    if hasattr(plc, "SetAttenuationRadius"):
        plc.SetAttenuationRadius({light_radius_cm})
    if hasattr(plc, "SetSourceRadius"):
        plc.SetSourceRadius(800.0)
    if hasattr(plc, "SetVolumetricScatteringIntensity"):
        plc.SetVolumetricScatteringIntensity(8.0)

# Move both actors to the requested position.
nv = ur.CoreUObject.Vector(); nv.X=px; nv.Y=py; nv.Z=pz
if proj is not None:
    proj.K2_SetActorLocation(nv, False)
if proj_light is not None:
    proj_light.K2_SetActorLocation(nv, False)
print(f"PROJ_OK at ({{px:.0f}},{{py:.0f}},{{pz:.0f}})")
"""


def clear_projectile_code() -> str:
    return """
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
removed = 0
for cls, label in ((ur.Engine.StaticMeshActor, "_LightwarProjectile"),
                    (ur.Engine.PointLight,      "_LightwarProjectileLight")):
    for a in world.GetActorsOfClass(cls):
        try:
            if a.GetActorLabel() == label:
                a.K2_DestroyActor(); removed += 1
        except Exception: pass
print(f"PROJ_CLEAR removed={removed}")
"""


# ---------------------------------------------------------------------------
# Screenshot helper (mirrors light_war._common.take_screenshot signature)
# ---------------------------------------------------------------------------

async def take_screenshot(engine: InteractiveEngine, name: str,
                          workspace: Path, *, width: int = 640, height: int = 480,
                          screenshot_dir: Optional[Path] = None, wait_s: float = 0.7) -> Path:
    from veriworld.common.screenshot import capture_runtime_screenshot
    override = screenshot_dir or os.environ.get("LIGHTWAR_SCREENSHOT_DIR")
    return await capture_runtime_screenshot(engine, name, workspace,
        screenshot_dir=Path(override) if override else None,
        timeout=max(15.0, wait_s))


def unwrap(resp: dict) -> str:
    """Match light_war / object_grounding ``unwrap``: ``python_exec`` returns
    a nested ``{"result": {"result": <stdout_str>, ...}}`` dict; pull the
    inner string out, falling back to ``""`` for any unexpected shape."""
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


# ---------------------------------------------------------------------------
# Per-task randomization (launch, beacon position, beacon color).
# Same (task_id, seed) gives the same randomized task across all models,
# matching the object_ground view_seed contract.
# ---------------------------------------------------------------------------

# Hardcoded "safe-zone" envelope known to work in the v2 forest level.
# Launch covers a 30 × 30 km swath of the SW quadrant (much wider than
# the original 11 × 11 km box, which clustered every spawn on the same
# lake shore). Beacon stays in the elevated NE quadrant where hilltops
# go up to ~3300 cm. The two zones together cover the bulk of the
# walkable forest area, with min/max range chosen so the parabola
# still fits in (5..18 k cm/s, 10..25° pitch).
LAUNCH_ZONE = {
    "x": (0.0, 30000.0),
    "y": (15000.0, 45000.0),
    "z_above_ground_cm": 1500.0,
}
# Beacon zone widened to the entire NE quadrant + some extension —
# distant hills 1+ km away are now in scope, which lets the spatial-
# perception probe land beacons on tall ridges the model has to read
# off the preview as "really far".
BEACON_ZONE = {
    "x": (30000.0, 80000.0),
    "y": (45000.0, 80000.0),
}
MIN_RANGE_CM = 30000.0      # 300 m horizontal
MAX_RANGE_CM = 70000.0      # 700 m — beyond this the v2 forest fog
                            # eats the beacon glow even at 40M intensity
# Beacon must sit on a HIGH hill (forest floor sits ~ -1500..1500 cm,
# so 3000 cm puts the beacon clearly above tree-line silhouette).
BEACON_MIN_GROUND_Z_CM = 3000.0
# Min clearance for the launch camera — if there's a blocker within
# this many cm in the beacon-bearing direction, the camera is buried
# in foliage and we reject the spawn.
LAUNCH_FRONT_CLEAR_CM = 400.0

# Distinguishable beacon light colors. We deliberately avoid the warm
# red/orange band (projectile tracer colour) AND avoid white (blends
# with the bright forest sky in the v2 level). Tested 2026-05-03 —
# all five colours read clearly from a launch-site preview.
BEACON_COLOR_PALETTE = [
    {"name": "cyan",    "rgb": [0.30, 0.90, 1.00]},
    {"name": "magenta", "rgb": [1.00, 0.30, 0.90]},
    {"name": "green",   "rgb": [0.30, 1.00, 0.40]},
    {"name": "yellow",  "rgb": [1.00, 0.95, 0.20]},
    {"name": "purple",  "rgb": [0.70, 0.40, 1.00]},
]

# Beacon brightness tiers. ``intensity`` drives the PointLight's lumens
# at the source; ``attenuation_cm`` is how far the light reaches before
# falling to zero; ``vol_scatter`` is the air-scattering boost. The
# triple is calibrated so ``weak`` is "barely picks out the hilltop in
# fog at 500 m", ``medium`` is "clearly visible across the valley",
# ``strong`` is "cuts through fog and dense foliage from anywhere in
# the 30-700 m envelope". Verified visually 2026-05-03.
BEACON_INTENSITY_PROFILES = {
    "weak":   {"intensity": 5_000_000.0,  "attenuation_cm":  50_000.0,
               "source_radius": 30.0, "vol_scatter": 1.5},
    "medium": {"intensity": 15_000_000.0, "attenuation_cm":  90_000.0,
               "source_radius": 45.0, "vol_scatter": 2.0},
    "strong": {"intensity": 40_000_000.0, "attenuation_cm": 150_000.0,
               "source_radius": 60.0, "vol_scatter": 3.0},
}


def apply_beacon_intensity(beacon: dict, profile: str) -> dict:
    """Return a new beacon dict with the brightness-related fields
    overwritten by ``BEACON_INTENSITY_PROFILES[profile]``. Caller
    keeps everything else (XY, tower geometry, color)."""
    if profile not in BEACON_INTENSITY_PROFILES:
        raise ValueError(f"unknown beacon intensity profile: {profile!r}; "
                         f"choices: {list(BEACON_INTENSITY_PROFILES)}")
    new = dict(beacon)
    p = BEACON_INTENSITY_PROFILES[profile]
    new["light_intensity"]      = p["intensity"]
    new["light_attenuation_cm"] = p["attenuation_cm"]
    new["light_source_radius"]  = p["source_radius"]
    new["vol_scatter"]          = p["vol_scatter"]
    return new


def view_seed_for(task_id: str, seed: int) -> int:
    """Match object_ground's view_seed formula so randomization is
    deterministic across processes (Python's hash() is randomized;
    zlib.crc32 isn't)."""
    return (seed * 1009 + zlib.crc32(task_id.encode())) & 0x7FFFFFFF


def line_of_sight_code(sx: float, sy: float, sz: float,
                       ex: float, ey: float, ez: float) -> str:
    """Trace a ray from (sx,sy,sz) to (ex,ey,ez) using
    ``KismetSystemLibrary.LineTraceSingle``. Print
    ``LOS_HIT <distance_cm>`` if a blocker was found before the ray
    reached the end point, ``LOS_CLEAR`` if it reached the end without
    hitting anything. Used to verify the launch->beacon line is not
    blocked by intervening hills / trees before we commit to a pose."""
    return f"""
import unreal_runtime as ur, math
ksm = ur.Engine.KismetSystemLibrary
s = ur.CoreUObject.Vector(); s.X={sx}; s.Y={sy}; s.Z={sz}
e = ur.CoreUObject.Vector(); e.X={ex}; e.Y={ey}; e.Z={ez}
hit = ur.Engine.HitResult()
tc = ur.CoreUObject.LinearColor(); tc.R=1; tc.G=0; tc.B=0; tc.A=1
hc = ur.CoreUObject.LinearColor(); hc.R=0; hc.G=1; hc.B=0; hc.A=1
got = ksm.LineTraceSingle(s, e, ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
    False, [], 0, hit, True, tc, hc, 0.0)
if got and hit.bBlockingHit:
    p = hit.ImpactPoint
    d = math.sqrt((p.X-{sx})**2 + (p.Y-{sy})**2 + (p.Z-{sz})**2)
    print(f'LOS_HIT {{d:.1f}}')
else:
    print('LOS_CLEAR')
"""


def parse_los(out: str) -> Optional[float]:
    """Return hit distance in cm if blocked, ``-1.0`` for clear path,
    ``None`` if the trace returned nothing parseable."""
    for line in out.splitlines():
        if line.startswith("LOS_HIT"):
            return float(line.split()[1])
        if line == "LOS_CLEAR":
            return -1.0
    return None


async def randomize_task_pose(engine, spec: dict, view_seed: int,
                              *, max_tries: int = 48,
                              min_elev_deg: float = 3.0,
                              min_beacon_ground_z_cm: float = BEACON_MIN_GROUND_Z_CM,
                              los_clear_fraction: float = 0.85,
                              launch_front_clear_cm: float = LAUNCH_FRONT_CLEAR_CM,
                              intensity_profile: str = "medium",
                              tower_height_jitter_cm: float = 0.0,
                              ) -> dict:
    """Pick a (launch, beacon) pair + a beacon light color that fits
    inside the safe zones, has horizontal range in
    ``MIN_RANGE_CM..MAX_RANGE_CM``, sits on terrain above
    ``min_beacon_ground_z_cm`` (so the beacon is on a hill, not a
    swale), clears a minimum elevation angle, AND has at least
    ``los_clear_fraction`` of the launch->beacon line of sight not
    blocked by intervening terrain / trees.

    Uses live ground raycasts + a single LOS raycast per candidate
    via ``engine.python_exec``. Returns a dict suitable for
    overriding ``spec``'s ``launch_pose`` / ``beacon`` keys.

    ``intensity_profile``: ``"weak"`` / ``"medium"`` / ``"strong"`` for
    a fixed brightness tier, or ``"random"`` to pick one per
    ``view_seed`` (still deterministic — same seed → same pick across
    both harnesses, preserving the ablation contract).

    ``tower_height_jitter_cm``: if > 0, the beacon's tower height is
    perturbed by ``rng.uniform(-jitter, +jitter)`` per ``view_seed``.
    Default 0 keeps the spec's hardcoded tower height. The rng draws
    for these two params only happen when randomization is requested,
    so default callers keep byte-equivalent position picks."""
    rng = random.Random(view_seed)
    color = rng.choice(BEACON_COLOR_PALETTE)
    if intensity_profile == "random":
        chosen_intensity = rng.choice(list(BEACON_INTENSITY_PROFILES.keys()))
    else:
        chosen_intensity = intensity_profile
    if tower_height_jitter_cm > 0.0:
        height_delta = rng.uniform(-tower_height_jitter_cm, tower_height_jitter_cm)
    else:
        height_delta = 0.0
    base_beacon = dict(spec["beacon"])             # copy to mutate
    base_tower_height = base_beacon["tower_height_cm"]
    jittered_tower_height = base_tower_height + height_delta

    last_reason = "no candidates"
    for attempt in range(max_tries):
        lx = rng.uniform(*LAUNCH_ZONE["x"])
        ly = rng.uniform(*LAUNCH_ZONE["y"])
        bx = rng.uniform(*BEACON_ZONE["x"])
        by = rng.uniform(*BEACON_ZONE["y"])
        h_range = math.hypot(bx - lx, by - ly)
        if not (MIN_RANGE_CM <= h_range <= MAX_RANGE_CM):
            last_reason = f"range {h_range:.0f} out of envelope"
            continue
        # Sample ground at launch + beacon.
        l_out = unwrap(await engine.python_exec(ground_z_at_code(lx, ly)))
        l_ground = parse_ground_z(l_out)
        if l_ground is None:
            last_reason = "launch ground miss"
            continue
        b_out = unwrap(await engine.python_exec(ground_z_at_code(bx, by)))
        b_ground = parse_ground_z(b_out)
        if b_ground is None:
            last_reason = "beacon ground miss"
            continue
        if b_ground < min_beacon_ground_z_cm:
            last_reason = (f"beacon ground_z {b_ground:.0f} below hill "
                           f"threshold {min_beacon_ground_z_cm:.0f}")
            continue
        launch_z = l_ground + LAUNCH_ZONE["z_above_ground_cm"]
        apex_z = (b_ground + jittered_tower_height
                  + base_beacon["light_gap_cm"])
        elev_deg = math.degrees(math.atan2(apex_z - launch_z, h_range))
        if elev_deg < min_elev_deg:
            last_reason = f"elev {elev_deg:.1f}° below min {min_elev_deg}°"
            continue
        # Line-of-sight from launch (camera height) to a point just above
        # the beacon's apex. If a hit comes back at less than
        # ``los_clear_fraction`` of full distance, something blocks the
        # view (tree, ridge, hill), reject.
        full_dist = math.sqrt((bx - lx) ** 2 + (by - ly) ** 2
                              + (apex_z + 200.0 - launch_z) ** 2)
        los_out = unwrap(await engine.python_exec(line_of_sight_code(
            lx, ly, launch_z, bx, by, apex_z + 200.0)))
        los_d = parse_los(los_out)
        if los_d is None:
            last_reason = f"LOS trace unparseable: {los_out!r}"
            continue
        if los_d >= 0.0 and los_d < los_clear_fraction * full_dist:
            last_reason = (f"LOS blocked at {los_d:.0f}/{full_dist:.0f} cm "
                           f"({los_d/full_dist:.0%} of way)")
            continue
        # Front-blocker check: short ray from launch pose along the
        # beacon bearing. If something blocks within
        # ``launch_front_clear_cm``, the camera is buried in dense
        # foliage / against a wall — reject so the preview frame
        # doesn't open with leaves filling the screen.
        ux = (bx - lx) / h_range; uy = (by - ly) / h_range
        front_x = lx + ux * launch_front_clear_cm
        front_y = ly + uy * launch_front_clear_cm
        front_z = launch_z                            # same camera height
        front_out = unwrap(await engine.python_exec(line_of_sight_code(
            lx, ly, launch_z, front_x, front_y, front_z)))
        front_d = parse_los(front_out)
        if front_d is None:
            last_reason = f"front-clear trace unparseable: {front_out!r}"
            continue
        if front_d >= 0.0:
            last_reason = (f"foliage in front of camera at {front_d:.0f} cm "
                           f"(< {launch_front_clear_cm:.0f} cm clearance)")
            continue
        new_beacon = dict(base_beacon)
        new_beacon["X"] = bx; new_beacon["Y"] = by
        new_beacon["light_color"] = color["rgb"]
        new_beacon["tower_height_cm"] = jittered_tower_height
        # Apply the intensity tier so both ``harness_arc`` and
        # ``harness_arc_visual_only`` see the same beacon brightness
        # for the same (task, seed) — keeping the harness ablation
        # clean.
        new_beacon = apply_beacon_intensity(new_beacon, chosen_intensity)
        return {
            "launch_pose": {"X": lx, "Y": ly, "Z": launch_z},
            "launch_ground_z": l_ground,
            "beacon": new_beacon,
            "beacon_ground_z": b_ground,
            "beacon_color_name": color["name"],
            "beacon_intensity_profile": chosen_intensity,
            "tower_height_cm": jittered_tower_height,
            "tower_height_delta_cm": height_delta,
            "horizontal_range_cm": h_range,
            "elevation_angle_deg": elev_deg,
            "los_blocked_at_cm": (None if los_d < 0 else los_d),
            "attempts": attempt + 1,
        }
    raise RuntimeError(
        f"could not sample valid randomized task within {max_tries} "
        f"tries; last failure: {last_reason}")


__all__ = [
    "load_groundtruth",
    "parabola_xyz",
    "find_landing",
    "evenly_spaced_waypoints",
    "velocity_to_yaw_pitch",
    "stack_frames",
    "OPEN_FOREST_CODE",
    "ground_z_at_code", "parse_ground_z",
    "line_of_sight_code", "parse_los",
    "spawn_beacon_code",
    "reset_pawn_pose_code",
    "spawn_projectile_marker_code",
    "clear_projectile_code",
    "take_screenshot",
    "unwrap",
    "view_seed_for",
    "randomize_task_pose",
    "BEACON_COLOR_PALETTE",
    "BEACON_INTENSITY_PROFILES",
    "apply_beacon_intensity",
]
