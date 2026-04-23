"""
setup_tunnel.py — Grid lattice walls + curved tunnel, parameterized.

Reads from builtins:
  _MAZE_PARAMS: dict (from generate_params)
  _TUNNEL_RADIUS: float (default 80.0, small=50.0)
  _TUNNEL_COLORFUL: bool (default True = 12 materials, False = uniform brick)
"""
import unreal_runtime as ur
import json
import random
import math
import builtins

params = getattr(builtins, '_MAZE_PARAMS', None)
if params is None:
    print("[ERROR] No _MAZE_PARAMS in builtins")
    raise RuntimeError("No maze params")

PIPE_RADIUS = getattr(builtins, '_TUNNEL_RADIUS', 80.0)
COLORFUL = getattr(builtins, '_TUNNEL_COLORFUL', True)
CEILING_TYPE = getattr(builtins, '_TUNNEL_CEILING', 'glass')  # "glass", "voxel", "none"

Z_OFFSET = 500.0
EYE_HEIGHT = 170.0
WALL_HEIGHT = 400.0

grid_rows = params["grid_rows"]
grid_cols = params["grid_cols"]
cell_size = params["cell_size"]
logical_rows = params["logical_rows"]
logical_cols = params["logical_cols"]

MAZE_W = grid_cols * cell_size
MAZE_H = grid_rows * cell_size
WALL_THICK = 40.0

def create_transform():
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X, v.Y, v.Z = 0, 0, 0
    if hasattr(t, 'Translation'):
        t.Translation = v
    r = ur.CoreUObject.Rotator(); r.Pitch = 0; r.Yaw = 0; r.Roll = 0
    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(r)
    s = ur.CoreUObject.Vector(); s.X = s.Y = s.Z = 1
    t.Scale3D = s
    return t

world = ur.Engine.GetDefaultWorld()

# Clean
for a in world.GetActorsOfClass(ur.Voxel.VoxelStampActor):
    try: a.DestroyActor()
    except: a.K2_DestroyActor()

vw = world.GetActorsOfClass(ur.Voxel.VoxelWorld)[0]
mm = vw.MegaMaterial
if mm is None:
    mm = ur.StaticLoadObject(ur.Voxel.VoxelMegaMaterial, None,
        "/Game/VoxelExamples/NaniteMaterials/Materials/test_mega_material.test_mega_material")
    vw.MegaMaterial = mm
st = mm.SurfaceTypes
default_layer = ur.StaticLoadObject(ur.Voxel.VoxelVolumeLayer, None,
    "/Voxel/Default/DefaultVolumeLayer.DefaultVolumeLayer")

rng = random.Random(99)

# ── Material setup ──
if COLORFUL:
    # Pure solid colors only (skip 25=white/floor, 26=red/goal, 27=green/start)
    AVAILABLE_MATS = [24, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37]
else:
    AVAILABLE_MATS = [5]  # uniform tile

# ── Grid lattice walls ──
panel_groups = {}
wz = Z_OFFSET + WALL_HEIGHT / 2.0
panel_idx = 0

# Vertical walls
for i in range(logical_cols + 1):
    wx = i * cell_size * 2
    wy = MAZE_H / 2.0
    mat = AVAILABLE_MATS[panel_idx % len(AVAILABLE_MATS)]
    panel_groups.setdefault(mat, []).append(
        f'            <Cube sizeX="{WALL_THICK}" sizeY="{MAZE_H}" '
        f'sizeZ="{WALL_HEIGHT}" x="{wx:.0f}" y="{wy:.0f}" z="{wz:.0f}"/>')
    panel_idx += 1

# Horizontal walls
for i in range(logical_rows + 1):
    wy = i * cell_size * 2
    wx = MAZE_W / 2.0
    mat = AVAILABLE_MATS[panel_idx % len(AVAILABLE_MATS)]
    panel_groups.setdefault(mat, []).append(
        f'            <Cube sizeX="{MAZE_W}" sizeY="{WALL_THICK}" '
        f'sizeZ="{WALL_HEIGHT}" x="{wx:.0f}" y="{wy:.0f}" z="{wz:.0f}"/>')
    panel_idx += 1

# ── Pick start and goal ──
start_cell = (0, 0)
goal_cell = (logical_rows - 1, logical_cols - 1)

def cell_to_world(lr, lc):
    """Grid walls at i*cell_size*2, cell center at (2*lc+1)*cell_size."""
    x = (lc * 2 + 1) * cell_size
    y = (lr * 2 + 1) * cell_size
    return x, y

sx, sy = cell_to_world(*start_cell)
gx, gy = cell_to_world(*goal_cell)
tunnel_z = Z_OFFSET + EYE_HEIGHT

# ── Generate curved tunnel ──
waypoints = [(sx, sy)]
num_mid = rng.randint(3, 5)
for i in range(num_mid):
    t = (i + 1) / (num_mid + 1)
    mx = sx + (gx - sx) * t
    my = sy + (gy - sy) * t
    offset = MAZE_W * 0.3
    mx += rng.uniform(-offset, offset)
    my += rng.uniform(-offset, offset)
    mx = max(cell_size, min(MAZE_W - cell_size, mx))
    my = max(cell_size, min(MAZE_H - cell_size, my))
    waypoints.append((mx, my))
waypoints.append((gx, gy))

# ── HermitePipe segments ──
pipe_segments = []
for i in range(len(waypoints) - 1):
    x1, y1 = waypoints[i]
    x2, y2 = waypoints[i + 1]
    dx = x2 - x1
    dy = y2 - y1
    dist = math.sqrt(dx*dx + dy*dy)
    vel_scale = dist * 1.5
    perp_x = -dy / (dist + 1) * vel_scale * rng.uniform(0.3, 0.8)
    perp_y = dx / (dist + 1) * vel_scale * rng.uniform(0.3, 0.8)
    svx = dx / (dist + 1) * vel_scale + perp_x
    svy = dy / (dist + 1) * vel_scale + perp_y
    evx = dx / (dist + 1) * vel_scale - perp_x
    evy = dy / (dist + 1) * vel_scale - perp_y
    z_var = rng.uniform(-30, 30)
    pipe_segments.append(
        f'            <HermitePipe\n'
        f'                startX="{x1:.0f}" startY="{y1:.0f}" startZ="{tunnel_z:.0f}"\n'
        f'                startVelX="{svx:.0f}" startVelY="{svy:.0f}" startVelZ="{z_var:.0f}"\n'
        f'                endX="{x2:.0f}" endY="{y2:.0f}" endZ="{tunnel_z + z_var:.0f}"\n'
        f'                endVelX="{evx:.0f}" endVelY="{evy:.0f}" endVelZ="{-z_var:.0f}"\n'
        f'                radius="{PIPE_RADIUS}"\n'
        f'                innerRadius="0"\n'
        f'                closedEnds="false"\n'
        f'                segments="40"\n'
        f'                smoothness="0"\n'
        f'            />')

chambers = []
for wx, wy in waypoints:
    chambers.append(
        f'            <Sphere radius="{PIPE_RADIUS * 1.5:.0f}" '
        f'centerX="{wx:.0f}" centerY="{wy:.0f}" centerZ="{tunnel_z:.0f}"/>')

# ── Build XML: Subtract pipes from walls ──
pipes_xml = "\n".join(pipe_segments + chambers)
K2 = ur.Voxel.VoxelBooleanTreeShape_K2

for mat_idx, panels in panel_groups.items():
    if not panels:
        continue
    panels_xml = "\n".join(panels)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Operation type="Subtract" smoothness="0">
        <Operation type="Union" smoothness="0">
{panels_xml}
        </Operation>
        <Operation type="Union" smoothness="0">
{pipes_xml}
        </Operation>
    </Operation>
</BooleanTree>'''
    err = ""
    root_node = K2.BuildBooleanTreeFromXML(xml, err)
    if root_node:
        s = ur.Voxel.VoxelShapeStampRef()
        K2.MakeBooleanTreeStamp(
            s, [root_node],
            ur.Voxel.EVoxelBooleanOperation.Union, 0.0,
            st[mat_idx], default_layer,
            ur.Voxel.EVoxelVolumeBlendMode.Additive,
            [], create_transform(),
            ur.Voxel.EVoxelStampBehavior.AffectAll,
            0, 0.0, ur.Voxel.VoxelMetadataOverrides(),
            ur.CoreUObject.Int32Interval(0, 32),
            False, True, 1.0)
        a = world.SpawnActorEx(ur.Voxel.VoxelStampActor, create_transform(), 1)
        if a: a.SetStamp(s); a.UpdateStamp(); a.SetActorLabel(f"MZ_Walls_mat{mat_idx}")

# ── Floor ──
floor_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Cube sizeX="{MAZE_W}" sizeY="{MAZE_H}" sizeZ="20"
          x="{MAZE_W/2:.0f}" y="{MAZE_H/2:.0f}" z="{Z_OFFSET - 10}"/>
</BooleanTree>'''
fe = ""
fn = K2.BuildBooleanTreeFromXML(floor_xml, fe)
if fn:
    fs = ur.Voxel.VoxelShapeStampRef()
    K2.MakeBooleanTreeStamp(
        fs, [fn], ur.Voxel.EVoxelBooleanOperation.Union, 0.0,
        st[25], default_layer, ur.Voxel.EVoxelVolumeBlendMode.Additive,
        [], create_transform(), ur.Voxel.EVoxelStampBehavior.AffectAll,
        0, 0.0, ur.Voxel.VoxelMetadataOverrides(),
        ur.CoreUObject.Int32Interval(0, 32), False, True, 1.0)
    fa = world.SpawnActorEx(ur.Voxel.VoxelStampActor, create_transform(), 1)
    if fa: fa.SetStamp(fs); fa.UpdateStamp(); fa.SetActorLabel("MZ_Floor")

# ── Start/Goal markers ──
MARKER_SIZE = cell_size * 2
for label, cx, cy, mi in [("MZ_Start", sx, sy, 27), ("MZ_Goal", gx, gy, 26)]:
    mx = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Cube sizeX="{MARKER_SIZE}" sizeY="{MARKER_SIZE}" sizeZ="22"
          x="{cx:.0f}" y="{cy:.0f}" z="{Z_OFFSET - 10}"/>
</BooleanTree>'''
    me = ""
    mn = K2.BuildBooleanTreeFromXML(mx, me)
    if mn:
        ms = ur.Voxel.VoxelShapeStampRef()
        K2.MakeBooleanTreeStamp(
            ms, [mn], ur.Voxel.EVoxelBooleanOperation.Union, 0.0,
            st[mi], default_layer, ur.Voxel.EVoxelVolumeBlendMode.Additive,
            [], create_transform(), ur.Voxel.EVoxelStampBehavior.AffectAll,
            0, 0.0, ur.Voxel.VoxelMetadataOverrides(),
            ur.CoreUObject.Int32Interval(0, 32), False, True, 1.0)
        ma = world.SpawnActorEx(ur.Voxel.VoxelStampActor, create_transform(), 1)
        if ma: ma.SetStamp(ms); ma.UpdateStamp(); ma.SetActorLabel(label)

# ── Camera at start ──
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns:
    pawn = pawns[0]
    ctrl = pawn.GetController()
    cam = ur.CoreUObject.Vector(); cam.X = sx; cam.Y = sy; cam.Z = Z_OFFSET + EYE_HEIGHT
    pawn.K2_SetActorLocation(cam, False)
    dx = waypoints[1][0] - sx
    dy = waypoints[1][1] - sy
    yaw = math.degrees(math.atan2(dy, dx))
    rot = ur.CoreUObject.Rotator(); rot.Pitch = -2; rot.Yaw = yaw; rot.Roll = 0
    ctrl.SetControlRotation(rot)

# ── Ceiling ──
if CEILING_TYPE == "voxel":
    # Solid voxel ceiling — blocks camera, light still renders fine inside
    ceiling_z_top = Z_OFFSET + WALL_HEIGHT
    ceiling_thick = 30.0

    ceiling_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Cube sizeX="{MAZE_W}" sizeY="{MAZE_H}" sizeZ="{ceiling_thick}"
          x="{MAZE_W/2:.0f}" y="{MAZE_H/2:.0f}" z="{ceiling_z_top:.0f}"/>
</BooleanTree>'''
    ce = ""
    cn = K2.BuildBooleanTreeFromXML(ceiling_xml, ce)
    if cn:
        cs = ur.Voxel.VoxelShapeStampRef()
        K2.MakeBooleanTreeStamp(
            cs, [cn], ur.Voxel.EVoxelBooleanOperation.Union, 0.0,
            st[25], default_layer, ur.Voxel.EVoxelVolumeBlendMode.Additive,
            [], create_transform(), ur.Voxel.EVoxelStampBehavior.AffectAll,
            0, 0.0, ur.Voxel.VoxelMetadataOverrides(),
            ur.CoreUObject.Int32Interval(0, 32), False, True, 1.0)
        ca = world.SpawnActorEx(ur.Voxel.VoxelStampActor, create_transform(), 1)
        if ca: ca.SetStamp(cs); ca.UpdateStamp(); ca.SetActorLabel("MZ_VoxelCeiling")
        print(f"  Voxel ceiling OK (solid)")
    else:
        print(f"  Voxel ceiling FAIL: {ce}")

elif CEILING_TYPE == "glass":
    BP_PATH = '/Game/BreakableGlass2/GlassCore/BP_BreakableGlass_Lite.BP_BreakableGlass_Lite'
    bp = ur.StaticLoadObject(ur.Engine.Blueprint, None, BP_PATH)
    if bp:
        bp_class = bp.GeneratedClass
        for a in world.GetActorsOfClass(bp_class):
            a.K2_DestroyActor()

        def make_glass_transform(x, y, z, pitch=0, yaw=0, roll=0, sx=1, sy=1, sz=1):
            t = ur.CoreUObject.Transform()
            loc = ur.CoreUObject.Vector(); loc.X, loc.Y, loc.Z = x, y, z
            t.Translation = loc
            rot = ur.CoreUObject.Rotator(); rot.Pitch = pitch; rot.Yaw = yaw; rot.Roll = roll
            t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(rot)
            s = ur.CoreUObject.Vector(); s.X, s.Y, s.Z = sx, sy, sz
            t.Scale3D = s
            return t

        def configure_glass(glass):
            root = getattr(glass, 'RootComponent', None)
            if not root: return
            try:
                root.SetCollisionEnabled(3)
                root.SetGenerateOverlapEvents(False)
                root.SetNotifyRigidBodyCollision(False)
                root.SetCollisionResponseToChannel(0, 2)
                root.SetCollisionResponseToChannel(1, 2)
                root.SetCollisionResponseToChannel(2, 2)
                root.SetCollisionResponseToChannel(4, 2)
                glass.SetActorEnableCollision(True)
            except: pass
            dyn_mat = getattr(glass, 'DynamicMaterial', None)
            if dyn_mat:
                dyn_mat.SetScalarParameterValue('Opacity', 0.03)

        ceiling_x = MAZE_W / 2.0
        ceiling_y = MAZE_H / 2.0
        ceiling_z = Z_OFFSET + WALL_HEIGHT
        scale_x = MAZE_W / 100.0
        scale_y = MAZE_H / 100.0
        t = make_glass_transform(ceiling_x, ceiling_y, ceiling_z,
                                 pitch=90, yaw=0, roll=0,
                                 sx=1, sy=scale_x, sz=scale_y)
        glass = world.SpawnActorEx(bp_class, t, 1)
        if glass:
            configure_glass(glass)
            glass.SetActorLabel("MZ_GlassCeiling")
            print("  Glass ceiling OK")
    else:
        print("  Glass BP not found in packaged build — skipped")
else:
    print("  No ceiling")

mat_mode = "colorful" if COLORFUL else "uniform"
print(f"[DONE] Tunnel maze, radius={PIPE_RADIUS}, materials={mat_mode}, waypoints={len(waypoints)}")
