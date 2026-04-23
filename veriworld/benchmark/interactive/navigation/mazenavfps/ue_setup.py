"""
setup.py — Spawn maze in UE. Reads params from builtins._MAZE_PARAMS.
Adapted from harness_scale/grid_maze_batchfree/setup.py for WebSocket use.
"""
import unreal_runtime as ur
import json, builtins

params = getattr(builtins, '_MAZE_PARAMS', None)
if params is None:
    print("[ERROR] No _MAZE_PARAMS in builtins")
    raise RuntimeError("No maze params")

world = ur.Engine.GetDefaultWorld()
K2 = ur.Voxel.VoxelBooleanTreeShape_K2

grid        = params["grid"]
grid_rows   = params["grid_rows"]
grid_cols   = params["grid_cols"]
cell_size   = params["cell_size"]
wall_height = params["wall_height"]
start_grid  = params["start_grid"]
goal_grid   = params["goal_grid"]
initial_yaw = params["initial_yaw"]

Z_OFFSET   = 500.0
EYE_HEIGHT = 170.0

# ── Materials ──
vw = world.GetActorsOfClass(ur.Voxel.VoxelWorld)[0]
mm = vw.MegaMaterial
if mm is None:
    mm = ur.StaticLoadObject(
        ur.Voxel.VoxelMegaMaterial, None,
        "/Game/VoxelExamples/NaniteMaterials/Materials/test_mega_material.test_mega_material")
    vw.MegaMaterial = mm

st = mm.SurfaceTypes
default_layer = ur.StaticLoadObject(
    ur.Voxel.VoxelVolumeLayer, None,
    "/Voxel/Default/DefaultVolumeLayer.DefaultVolumeLayer")

MATERIAL_COUNT = getattr(builtins, '_MAZE_MATERIALS', 3)
if MATERIAL_COUNT == 1:
    WALL_MATS = [7]
elif MATERIAL_COUNT == 2:
    WALL_MATS = [5, 7]
else:
    WALL_MATS = [5, 7, 8]

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

# ── Walls (grouped by material) ──
mat_groups = {m: [] for m in WALL_MATS}
for r in range(grid_rows):
    for c in range(grid_cols):
        if grid[r][c] == 1:
            wx = (c + 0.5) * cell_size
            wy = (r + 0.5) * cell_size
            wz = Z_OFFSET + wall_height / 2.0
            mat = WALL_MATS[(r + c) % len(WALL_MATS)]
            mat_groups[mat].append(
                f'        <Cube sizeX="{cell_size}" sizeY="{cell_size}" '
                f'sizeZ="{wall_height}" x="{wx:.0f}" y="{wy:.0f}" z="{wz:.0f}"/>')

for mat_idx, cubes in mat_groups.items():
    if not cubes:
        continue
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Operation type="Union" smoothness="0">
{chr(10).join(cubes)}
    </Operation>
</BooleanTree>'''
    err = ""
    root_node = K2.BuildBooleanTreeFromXML(xml, err)
    if root_node:
        stamp = ur.Voxel.VoxelShapeStampRef()
        K2.MakeBooleanTreeStamp(
            stamp, [root_node],
            ur.Voxel.EVoxelBooleanOperation.Union, 0.0,
            st[mat_idx], default_layer,
            ur.Voxel.EVoxelVolumeBlendMode.Additive,
            [], create_transform(),
            ur.Voxel.EVoxelStampBehavior.AffectAll,
            0, 0.0, ur.Voxel.VoxelMetadataOverrides(),
            ur.CoreUObject.Int32Interval(0, 32),
            False, True, 1.0)
        actor = world.SpawnActorEx(ur.Voxel.VoxelStampActor, create_transform(), 1)
        if actor:
            actor.SetStamp(stamp)
            actor.UpdateStamp()
            actor.SetActorLabel(f"MZ_Walls_{mat_idx}")

print(f"[walls] {sum(len(v) for v in mat_groups.values())} cubes, 3 material(s)")

# ── Floor ──
floor_cx = grid_cols * cell_size / 2.0
floor_cy = grid_rows * cell_size / 2.0
floor_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Cube sizeX="{grid_cols * cell_size}" sizeY="{grid_rows * cell_size}" sizeZ="20"
          x="{floor_cx:.0f}" y="{floor_cy:.0f}" z="{Z_OFFSET - 10}"/>
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
for label, gc, mi in [("MZ_Start", start_grid, 27), ("MZ_Goal", goal_grid, 26)]:
    gx = (gc[1] + 0.5) * cell_size
    gy = (gc[0] + 0.5) * cell_size
    mx = f'''<?xml version="1.0" encoding="UTF-8"?>
<BooleanTree>
    <Cube sizeX="{cell_size*0.8}" sizeY="{cell_size*0.8}" sizeZ="22"
          x="{gx:.0f}" y="{gy:.0f}" z="{Z_OFFSET-10}"/>
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

# ── Camera ──
sx = (start_grid[1] + 0.5) * cell_size
sy = (start_grid[0] + 0.5) * cell_size
game_mode = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(game_mode.DefaultPawnClass)
if pawns:
    pawn = pawns[0]
    ctrl = pawn.GetController()
    cam = ur.CoreUObject.Vector()
    cam.X = sx; cam.Y = sy; cam.Z = Z_OFFSET + EYE_HEIGHT
    pawn.K2_SetActorLocation(cam, False)
    rot = ur.CoreUObject.Rotator()
    rot.Pitch = -2; rot.Yaw = initial_yaw; rot.Roll = 0
    ctrl.SetControlRotation(rot)

print(f"[DONE] Maze {grid_rows}x{grid_cols}, start={start_grid}, goal={goal_grid}")
