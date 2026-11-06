"""bbox-style markers for object_ground harnesses.

Each query draws a 12-edge wireframe cube centred at (x, y, z) instead
of a sphere with 3 axis arrows:

  - X-running edges (4) — RED   (full length on X, thin on Y/Z)
  - Y-running edges (4) — GREEN
  - Z-running edges (4) — BLUE
  - one numeric idx label at the cube centre, in the ball's
    identifying colour (so multiple bboxes are still distinguishable)
  - one +X / +Y / +Z text label per axis, just past the +face

Compared to the sphere-with-arrows marker
(``object_grounding._common.spawn_anchored_markers_code``) this gives the
agent a directly readable axis-aligned reference frame at every ball
and removes the central sphere mass that can occlude the underlying
target. Designed for the off-centre harnesses
(``harness_grid_iterate_offcenter`` / ``harness_single_iterate_offcenter``)
where the agent must judge the target's offset from each anchor in
3-D rather than just "snap to the closest grid idx".

Helper signatures match
``object_grounding._common.spawn_anchored_markers_code`` /
``clear_query_markers_code`` so call sites only swap the function name.
"""

from __future__ import annotations

from ._common import QUERY_MARKER_COLORS


def spawn_bbox_markers_code(queries: list,
                             *, edge_len_cm: float = 16.0,
                             thickness_cm: float = 0.6,
                             label_size_cm: float = 6.0,
                             axis_label_size_cm: float = 3.5) -> str:
    """Spawn a 12-edge wireframe cube at every query in ``queries``.

    Each query dict must have ``i`` (int idx) and ``x``/``y``/``z`` (cm).
    Optional ``color`` (string in :data:`QUERY_MARKER_COLORS` keys)
    drives the central numeric label's colour; defaults to ``"red"``.
    Multiple queries get distinct labels so the agent can tell bboxes
    apart even though all 12 edges share the same XYZ palette.

    Idempotent — wipes any prior actor whose label starts with
    ``BBoxAxis_`` first.
    """
    # Pre-resolve color names to RGB float triples + integer triples (for
    # the TextRenderActor color setter). This keeps the UE-side script
    # purely numeric — no Python-side palette lookup at exec time.
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

QUERIES = {resolved_queries!r}

EL = {edge_len_cm}
H  = EL / 2.0
TH = {thickness_cm}
LBL_MAIN = {label_size_cm}
LBL_AXIS = {axis_label_size_cm}

EL_S = EL / 100.0     # cube mesh is 100 cm side at scale 1
TH_S = TH / 100.0

# Stable axis-edge palette: red on X, green on Y, blue on Z.
COL_X = (1.00, 0.10, 0.10)
COL_Y = (0.05, 1.00, 0.15)
COL_Z = (0.20, 0.40, 1.00)

# Edge templates (centre offset, scale on cube mesh, axis colour, axis tag).
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
    try:
        a.SetActorLabel(label)
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

def spawn_text(cx, cy, cz, text, rgb_int, world_size, label):
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X = cx; v.Y = cy; v.Z = cz
    t.Translation = v
    t.Rotation = kml.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(90.0, 180.0, 0.0))
    t.Scale3D = ur.CoreUObject.Vector(1, 1, 1)
    lbl = world.SpawnActorEx(ur.Engine.TextRenderActor, t, 1)
    if lbl is None: return
    try:
        lbl.SetActorLabel(label)
    except Exception: pass
    tr = lbl.TextRender
    tr.K2_SetText(text)
    tr.WorldSize = world_size
    tr.SetTextRenderColor(ur.CoreUObject.Color(
        rgb_int[0], rgb_int[1], rgb_int[2], 255))
    tr.HorizontalAlignment = ur.Engine.EHorizTextAligment.EHTA_Center
    tr.VerticalAlignment   = ur.Engine.EVerticalTextAligment.EVRTA_TextCenter

PAD = 2.5
for q in QUERIES:
    cx, cy, cz = q["x"], q["y"], q["z"]
    idx = q["i"]
    rgb_int = q["rgb_int"]
    # 12 axis-coloured edges.
    for off, scale_xyz, rgb, axis_tag in EDGES:
        spawn_bar(cx, cy, cz, off, scale_xyz, rgb,
                  f"BBoxAxis_e{{idx}}_{{axis_tag}}")
    # Numeric idx label at the cube centre, in the ball's identifying
    # colour (so the agent can still tell bboxes apart by colour even
    # though every cube shares the same XYZ palette).
    spawn_text(cx, cy, cz, str(idx), rgb_int, LBL_MAIN,
               f"BBoxAxis_idx{{idx}}")
    # +X / +Y / +Z labels just past the corresponding +face. These
    # only need to be visible enough to anchor orientation; they're
    # the SAME on every cube, so we keep the size small.
    spawn_text(cx + H + PAD, cy, cz, "+X", (255,  30,  30),
               LBL_AXIS, f"BBoxAxis_lblX_{{idx}}")
    spawn_text(cx, cy + H + PAD, cz, "+Y", ( 40, 255,  50),
               LBL_AXIS, f"BBoxAxis_lblY_{{idx}}")
    spawn_text(cx, cy, cz + H + PAD, "+Z", ( 70, 110, 255),
               LBL_AXIS, f"BBoxAxis_lblZ_{{idx}}")

print(f"BBOX_MARKERS_OK n={{len(QUERIES)}} edge_len={{EL:.1f}} cm")
"""


def clear_bbox_markers_code() -> str:
    """Idempotent wipe of every actor whose label starts with
    ``BBoxAxis_`` (matches both the edge bars and the labels
    spawned by :func:`spawn_bbox_markers_code`)."""
    return """
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
n = 0
for cls in (ur.Engine.StaticMeshActor, ur.Engine.TextRenderActor):
    for a in world.GetActorsOfClass(cls):
        try:
            if a.GetActorLabel().startswith("BBoxAxis_"):
                a.K2_DestroyActor(); n += 1
        except Exception: pass
print(f"BBOX_MARKERS_CLEARED n={n}")
"""


__all__ = ["spawn_bbox_markers_code", "clear_bbox_markers_code"]
