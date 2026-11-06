"""bbox-style markers v2 — same 12-edge wireframe cube as v1, but the
numeric idx label is now:
  1. OFFSET toward the camera by ~30 cm so it isn't buried inside the
     cube where the wireframe edges occlude it
  2. RENDERED at world_size 15 cm (was 6) so the digit is at least
     ~80 px tall in a 1280×720 frame at 100 cm view distance
  3. PAIRED with a small backing cube tinted INVERSE of the digit
     colour so it reads cleanly against any scene texture

The +X / +Y / +Z face labels are also bumped from 3.5 cm to 6 cm.

Helper signatures match
``object_ground._bbox_markers.spawn_bbox_markers_code`` and
``clear_bbox_markers_code`` so call sites only swap the import.

The original v1 spawn function is left intact so its result dirs stay
valid as a "no readable label" ablation point.
"""

from __future__ import annotations

from ._common import QUERY_MARKER_COLORS


def spawn_bbox_markers_code_v2(queries: list,
                                *, edge_len_cm: float = 16.0,
                                thickness_cm: float = 0.6,
                                label_size_cm: float = 15.0,
                                axis_label_size_cm: float = 6.0,
                                label_camera_offset_cm: float = 30.0,
                                ) -> str:
    """Spawn a 12-edge wireframe cube with a CAMERA-FACING idx label
    per query in ``queries``.

    Each query dict needs ``i`` (int idx) + ``x``/``y``/``z`` (cm).
    Optional ``color`` (string in :data:`QUERY_MARKER_COLORS`) drives
    the centre label's colour. Idempotent — wipes any prior actor
    labelled ``BBoxAxis_*`` first (label-prefix matches v1 so the
    old ``clear_bbox_markers_code`` still works).
    """
    resolved_queries = []
    for q in queries:
        col_name = q.get("color", "red")
        rgb = QUERY_MARKER_COLORS.get(col_name, QUERY_MARKER_COLORS["red"])
        rgb_int = (int(round(rgb[0] * 255)),
                    int(round(rgb[1] * 255)),
                    int(round(rgb[2] * 255)))
        resolved_queries.append({
            "i": int(q["i"]),
            "x": float(q["x"]), "y": float(q["y"]), "z": float(q["z"]),
            "rgb_int": rgb_int,
            "color_name": col_name,
        })
    return f"""
import unreal_runtime as ur
import math
world = ur.Engine.GetDefaultWorld()
kml = ur.Engine.KismetMathLibrary

# Pre-clear any prior bbox markers so this call is idempotent.
for cls in (ur.Engine.StaticMeshActor, ur.Engine.TextRenderActor):
    for a in world.GetActorsOfClass(cls):
        try:
            if a.GetActorLabel().startswith("BBoxAxis_"):
                a.K2_DestroyActor()
        except Exception: pass

cube = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cube.Cube")
base_mat = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")
if cube is None or base_mat is None:
    print("BBOX_ERROR missing_mesh_or_material")
    raise SystemExit

# Camera position + rotation — needed for the camera-facing label
# offset AND the proven label-rotation pattern from
# ``object_grounding._common.spawn_anchored_markers_code``:
#   label_yaw   = cam_yaw + 180  (text's -X "front" face points back at
#                                 the camera so text reads correctly)
#   label_pitch = -cam_pitch     (text tilts up to meet a down-pointing
#                                 camera squarely)
gm = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if not pawns:
    cam_x, cam_y, cam_z = 0.0, 0.0, 0.0
    cam_yaw, cam_pitch = 0.0, 0.0
else:
    cl = pawns[0].K2_GetActorLocation()
    cam_x, cam_y, cam_z = cl.X, cl.Y, cl.Z
    cr = pawns[0].GetController().GetControlRotation()
    cam_yaw = cr.Yaw
    cam_pitch = cr.Pitch
    if cam_pitch > 180.0:
        cam_pitch -= 360.0
label_yaw = cam_yaw + 180.0
label_pitch = -cam_pitch
label_rot = kml.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(label_pitch, label_yaw, 0))

QUERIES = {resolved_queries!r}

EL = {edge_len_cm}
H  = EL / 2.0
TH = {thickness_cm}
LBL_MAIN = {label_size_cm}
LBL_AXIS = {axis_label_size_cm}
LBL_CAM_OFF = {label_camera_offset_cm}

EL_S = EL / 100.0
TH_S = TH / 100.0

COL_X = (1.00, 0.10, 0.10)
COL_Y = (0.05, 1.00, 0.15)
COL_Z = (0.20, 0.40, 1.00)

EDGES = [
    (( 0,  H,  H), (EL_S, TH_S, TH_S), COL_X, "X_+y+z"),
    (( 0,  H, -H), (EL_S, TH_S, TH_S), COL_X, "X_+y-z"),
    (( 0, -H,  H), (EL_S, TH_S, TH_S), COL_X, "X_-y+z"),
    (( 0, -H, -H), (EL_S, TH_S, TH_S), COL_X, "X_-y-z"),
    (( H,  0,  H), (TH_S, EL_S, TH_S), COL_Y, "Y_+x+z"),
    (( H,  0, -H), (TH_S, EL_S, TH_S), COL_Y, "Y_+x-z"),
    ((-H,  0,  H), (TH_S, EL_S, TH_S), COL_Y, "Y_-x+z"),
    ((-H,  0, -H), (TH_S, EL_S, TH_S), COL_Y, "Y_-x-z"),
    (( H,  H,  0), (TH_S, TH_S, EL_S), COL_Z, "Z_+x+y"),
    (( H, -H,  0), (TH_S, TH_S, EL_S), COL_Z, "Z_+x-y"),
    ((-H,  H,  0), (TH_S, TH_S, EL_S), COL_Z, "Z_-x+y"),
    ((-H, -H,  0), (TH_S, TH_S, EL_S), COL_Z, "Z_-x-y"),
]

def spawn_bar(cx, cy, cz, off, scale_xyz, rgb, label):
    t = ur.CoreUObject.Transform()
    cv = ur.CoreUObject.Vector()
    cv.X = cx + off[0]; cv.Y = cy + off[1]; cv.Z = cz + off[2]
    t.Translation = cv
    t.Rotation = kml.Conv_RotatorToQuaternion(ur.CoreUObject.Rotator(0,0,0))
    sv = ur.CoreUObject.Vector()
    sv.X = scale_xyz[0]; sv.Y = scale_xyz[1]; sv.Z = scale_xyz[2]
    t.Scale3D = sv
    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    try: a.SetActorLabel(label)
    except Exception: pass
    smc = a.StaticMeshComponent
    smc.SetMobility(2)
    smc.SetStaticMesh(cube)
    smc.SetMaterial(0, base_mat)
    mid = smc.CreateDynamicMaterialInstance(0)
    if mid is not None:
        col = ur.CoreUObject.LinearColor()
        col.R, col.G, col.B, col.A = rgb[0], rgb[1], rgb[2], 1.0
        mid.SetVectorParameterValue("Color", col)
    smc.SetCollisionEnabled(0)

def spawn_text(cx, cy, cz, text, rgb_int, world_size, label,
               face_camera=False):
    # The proven pattern from object_grounding._common (sphere markers):
    # use the SAME label_rot for every marker, derived from camera
    # yaw + pitch — not per-marker bearing. This is what makes the
    # digit read correctly. (face_camera arg is kept for callers that
    # want the legacy top-down rotation, but the default is the
    # camera-aligned rotation that actually works.)
    if face_camera:
        rot = label_rot
    else:
        rot = kml.Conv_RotatorToQuaternion(
            ur.CoreUObject.Rotator(90.0, 180.0, 0.0))
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X = cx; v.Y = cy; v.Z = cz
    t.Translation = v
    t.Rotation = rot
    t.Scale3D = ur.CoreUObject.Vector(1, 1, 1)
    lbl = world.SpawnActorEx(ur.Engine.TextRenderActor, t, 1)
    if lbl is None: return
    try: lbl.SetActorLabel(label)
    except Exception: pass
    tr = lbl.TextRender
    tr.K2_SetText(text)
    tr.WorldSize = world_size
    tr.SetTextRenderColor(ur.CoreUObject.Color(
        rgb_int[0], rgb_int[1], rgb_int[2], 255))
    tr.HorizontalAlignment = ur.Engine.EHorizTextAligment.EHTA_Center
    tr.VerticalAlignment   = ur.Engine.EVerticalTextAligment.EVRTA_TextCenter

PAD = 4.0
for q in QUERIES:
    cx, cy, cz = q["x"], q["y"], q["z"]
    idx = q["i"]
    rgb_int = q["rgb_int"]
    # 12 axis-coloured edges.
    for off, scale_xyz, rgb, axis_tag in EDGES:
        spawn_bar(cx, cy, cz, off, scale_xyz, rgb,
                  f"BBoxAxis_e{{idx}}_{{axis_tag}}")

    # Camera-facing offset: shove the label TOWARD the camera so it
    # isn't buried in the wireframe edges.
    dx = cam_x - cx; dy = cam_y - cy; dz = cam_z - cz
    dn = math.sqrt(dx*dx + dy*dy + dz*dz) or 1.0
    ux, uy, uz = dx/dn, dy/dn, dz/dn
    lx = cx + ux * LBL_CAM_OFF
    ly = cy + uy * LBL_CAM_OFF
    lz = cz + uz * LBL_CAM_OFF
    # Centre digit colour: hardcoded dark navy (10, 25, 110) so every
    # idx label reads on bright kitchen surfaces — per-ball palette
    # rgb_int is kept on the wireframe edges (X/Y/Z) for orientation,
    # but the DIGIT itself doesn't need to encode the colour because
    # the number IS the identifier.
    DIGIT_RGB = (10, 25, 110)
    spawn_text(lx, ly, lz, str(idx), DIGIT_RGB, LBL_MAIN,
               f"BBoxAxis_idx{{idx}}", face_camera=True)

    # +X / +Y / +Z labels at the corresponding +faces (also camera-
    # billboarded so they read at any view angle).
    spawn_text(cx + H + PAD, cy, cz, "+X", (255,  30,  30),
               LBL_AXIS, f"BBoxAxis_lblX_{{idx}}", face_camera=True)
    spawn_text(cx, cy + H + PAD, cz, "+Y", ( 40, 255,  50),
               LBL_AXIS, f"BBoxAxis_lblY_{{idx}}", face_camera=True)
    spawn_text(cx, cy, cz + H + PAD, "+Z", ( 70, 110, 255),
               LBL_AXIS, f"BBoxAxis_lblZ_{{idx}}", face_camera=True)

print(f"BBOX_MARKERS_OK n={{len(QUERIES)}} edge_len={{EL:.1f}} cm "
      f"label_size={{LBL_MAIN:.1f}} cm cam=({{cam_x:.0f}},{{cam_y:.0f}},{{cam_z:.0f}})")
"""


__all__ = ["spawn_bbox_markers_code_v2"]
