"""Shared building blocks for `object_grounding` harnesses (and the smoke test).

Pipeline (validated by `Plugins/UELivePy/Tests/python/api/examples/apartment/`
probes 09-21):

  1. OpenLevel(`/Game/Levels/Day/Living_room_day.Living_room_day`) — async,
     sleep ~8 s after.
  2. Place pawn at `scene_start_pose` from `groundtruth.json` (the canonical
     starting camera vantage; not the UE level default).
  3. Find the placed actor by mesh leaf name. `mesh.GetPathName()` silently
     fails on this build — read the leaf from `str(mesh).strip("<>")`.
  4. `smc.SetMobility(2)` (Movable) before any `K2_SetActorLocation`, or the
     mutation no-ops on editor-placed Static actors.
  5. Verify by reading `K2_GetActorLocation` / `K2_GetActorRotation` and
     comparing against `groundtruth.json`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent
log = logging.getLogger(__name__)

GROUNDTRUTH_PATH = HERE / "groundtruth.json"
ALIASES_PATH = HERE / "aliases.json"


def load_groundtruth() -> dict:
    return json.loads(GROUNDTRUTH_PATH.read_text(encoding="utf-8"))


def load_aliases() -> dict:
    return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))


def resolve_camera_view(spec: dict, view_name: str, fallback: dict) -> dict:
    """Pick a named camera pose from `spec.camera_views`.

    Two schema styles are supported:
      - new (object_grounding): ``spec.camera_views = {<name>: pose, ...}``
      - legacy (object_ground): ``spec.camera_start_pose = pose`` —
        treated as the ``"default"`` view.

    Falls back to ``fallback`` (typically ``groundtruth["scene_start_pose"]``)
    if neither schema has the requested view. Logs a warning when an
    explicitly-requested view is missing.
    """
    views = spec.get("camera_views") or {}
    if view_name in views:
        return views[view_name]
    # Legacy single-view schema: spec.camera_start_pose ~= camera_views.default.
    if view_name == "default" and "camera_start_pose" in spec:
        return spec["camera_start_pose"]
    if views and view_name != "default":
        log.warning("camera view %r not in task views %s; using fallback",
                    view_name, list(views.keys()))
    return fallback


def resolve_alias(alias_field: str, aliases: dict) -> str:
    """`alias_field` may be `SM_blue_mug` or `SM_blue_mug | SM_white_grey_mug`.
    Pick the first listed alias and return its `real` leaf name (lowercased,
    suitable for matching against `str(mesh).strip("<>").rsplit(...)`)."""
    first = alias_field.split("|", 1)[0].strip()
    info = aliases["aliases"].get(first)
    if info is None:
        raise KeyError(f"alias not found: {first!r}")
    return info["real"].lower()


# ---------------------------------------------------------------------------
# UE snippets
# ---------------------------------------------------------------------------
APARTMENT_LEVEL = "/Game/Levels/Day/Living_room_day.Living_room_day"

OPEN_APARTMENT_CODE = f"""\
import unreal_runtime as ur
cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)
TARGET = "{APARTMENT_LEVEL}"
if cur and cur in TARGET:
    print("LEVEL_READY")
else:
    print("LEVEL_LOADING")
    ur.Engine.GameplayStatics.OpenLevel(TARGET, True, "")
"""


def reset_pawn_code(start_pose: dict) -> str:
    loc = start_pose["location"]
    rot = start_pose["control_rotation"]
    return f"""\
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
gm = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if not pawns:
    print("PAWN_MISSING"); raise SystemExit
p = pawns[0]
v = ur.CoreUObject.Vector(); v.X={loc["X"]}; v.Y={loc["Y"]}; v.Z={loc["Z"]}
p.K2_SetActorLocation(v, False)
r = ur.CoreUObject.Rotator(); r.Pitch={rot["Pitch"]}; r.Yaw={rot["Yaw"]}; r.Roll={rot["Roll"]}
p.GetController().SetControlRotation(r)
print(f"PAWN_RESET {loc['X']:.1f} {loc['Y']:.1f} {loc['Z']:.1f} "
      f"{rot['Yaw']:.2f} {rot['Pitch']:.2f}")
"""


def find_and_prep_object_code(mesh_leaf_lower: str) -> str:
    """Find the StaticMeshActor whose mesh leaf matches, set Mobility=2,
    print `OBJ_FOUND <x> <y> <z> <pitch> <yaw> <roll>` or `OBJ_NOT_FOUND`."""
    return f"""\
import unreal_runtime as ur
TARGET = "{mesh_leaf_lower}"

def mesh_leaf(smc):
    mesh = None
    try: mesh = smc.StaticMesh
    except Exception: pass
    if mesh is None:
        try: mesh = smc.GetStaticMesh()
        except Exception: return None
    if mesh is None: return None
    return str(mesh).strip("<>").rsplit(".", 1)[-1].rsplit("/", 1)[-1].lower()

world = ur.Engine.GetDefaultWorld()
hit = None
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        smc = a.StaticMeshComponent
        if smc and mesh_leaf(smc) == TARGET:
            hit = (a, smc); break
    except Exception:
        continue

if hit is None:
    print("OBJ_NOT_FOUND")
else:
    actor, smc = hit
    try: smc.SetMobility(2)
    except Exception as e: print(f"MOBILITY_ERR: {{e}}")
    label = actor.GetActorLabel()
    L = actor.K2_GetActorLocation()
    R = actor.K2_GetActorRotation()
    print(f"OBJ_FOUND label={{label}} {{L.X:.2f}} {{L.Y:.2f}} {{L.Z:.2f}} "
          f"{{R.Pitch:.2f}} {{R.Yaw:.2f}} {{R.Roll:.2f}}")
"""


def move_object_code(mesh_leaf_lower: str, x: float, y: float, z: float,
                     rot: Optional[dict] = None) -> str:
    """Set the actor's location (always) and rotation (if `rot` given)."""
    # Single line on purpose: this string is inserted at one indented
    # position in the template; embedded newlines would break Python's
    # block indentation in the emitted source.
    if rot is not None:
        rot_block = (
            f"r = ur.CoreUObject.Rotator(); "
            f"r.Pitch={rot['Pitch']}; r.Yaw={rot['Yaw']}; r.Roll={rot['Roll']}; "
            "actor.K2_SetActorRotation(r, False)"
        )
    else:
        rot_block = "pass  # no rotation requested"
    return f"""\
import unreal_runtime as ur
TARGET = "{mesh_leaf_lower}"

def mesh_leaf(smc):
    mesh = None
    try: mesh = smc.StaticMesh
    except Exception: pass
    if mesh is None:
        try: mesh = smc.GetStaticMesh()
        except Exception: return None
    if mesh is None: return None
    return str(mesh).strip("<>").rsplit(".", 1)[-1].rsplit("/", 1)[-1].lower()

world = ur.Engine.GetDefaultWorld()
actor = None; smc = None
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        c = a.StaticMeshComponent
        if c and mesh_leaf(c) == TARGET:
            actor = a; smc = c; break
    except Exception:
        continue

if actor is None:
    print("MOVE_NOT_FOUND")
else:
    try: smc.SetMobility(2)
    except Exception: pass
    v = ur.CoreUObject.Vector(); v.X={x}; v.Y={y}; v.Z={z}
    actor.K2_SetActorLocation(v, False)
    {rot_block}
    L = actor.K2_GetActorLocation()
    R = actor.K2_GetActorRotation()
    print(f"MOVE_OK {{L.X:.2f}} {{L.Y:.2f}} {{L.Z:.2f}} "
          f"{{R.Pitch:.2f}} {{R.Yaw:.2f}} {{R.Roll:.2f}}")
"""


DEFAULT_RAY_LENGTH_CM = 200.0


def build_raycast_code(mesh_leaf_lower: str,
                       length_cm: float = DEFAULT_RAY_LENGTH_CM) -> str:
    """Cast 6 world-axis-aligned rays from the object's center (±X/±Y/±Z)
    and print one ``RAYCAST:`` line.

    Each direction reports either ``hit@<dist>cm:<mesh_leaf>`` (closest
    hit, ignoring the moved object itself) or ``open`` if nothing within
    ``length_cm``. Format: ``RAYCAST:+X=...|-X=...|+Y=...|-Y=...|+Z=...|-Z=...``.
    """
    return f"""\
import unreal_runtime as ur
TARGET = "{mesh_leaf_lower}"
L = {length_cm}

def mesh_leaf(smc):
    mesh = None
    try: mesh = smc.StaticMesh
    except Exception: pass
    if mesh is None:
        try: mesh = smc.GetStaticMesh()
        except Exception: return None
    if mesh is None: return None
    return str(mesh).strip("<>").rsplit(".", 1)[-1].rsplit("/", 1)[-1]

def hit_leaf(actor):
    if actor is None: return ""
    try:
        smc = actor.StaticMeshComponent
        if smc:
            leaf = mesh_leaf(smc)
            if leaf: return leaf
    except Exception:
        pass
    try: return actor.GetActorLabel()
    except Exception: return ""

world = ur.Engine.GetDefaultWorld()
target_actor = None
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        c = a.StaticMeshComponent
        if c and (mesh_leaf(c) or "").lower() == TARGET:
            target_actor = a; break
    except Exception:
        continue
if target_actor is None:
    print("RAYCAST_NOT_FOUND")
else:
    loc = target_actor.K2_GetActorLocation()
    ksm = ur.Engine.KismetSystemLibrary
    DIRS = [("+X",1,0,0),("-X",-1,0,0),("+Y",0,1,0),("-Y",0,-1,0),
            ("+Z",0,0,1),("-Z",0,0,-1)]
    out = []
    for name, dx, dy, dz in DIRS:
        start = ur.CoreUObject.Vector(); start.X=loc.X; start.Y=loc.Y; start.Z=loc.Z
        end   = ur.CoreUObject.Vector()
        end.X = loc.X + dx*L; end.Y = loc.Y + dy*L; end.Z = loc.Z + dz*L
        hit = ur.Engine.HitResult()
        tc = ur.CoreUObject.LinearColor(); tc.R=1;tc.G=0;tc.B=0;tc.A=1
        hc = ur.CoreUObject.LinearColor(); hc.R=0;hc.G=1;hc.B=0;hc.A=1
        # ActorsToIgnore = [target_actor] so the ray doesn't immediately
        # hit the moved object's own bounds.
        got = ksm.LineTraceSingle(start, end, ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
            False, [target_actor], ur.Engine.EDrawDebugTrace.ForOneFrame, hit, True, tc, hc, 1.0)
        if got and hit.bBlockingHit:
            leaf = hit_leaf(hit.HitActor) if hasattr(hit, "HitActor") else ""
            out.append(f"{{name}}=hit@{{hit.Distance:.0f}}cm:{{leaf}}")
        else:
            out.append(f"{{name}}=open")
    print("RAYCAST:" + "|".join(out))
"""


def parse_raycast_line(stdout: str) -> Optional[dict]:
    """Parse the ``RAYCAST:`` line into ``{dir: {hit, dist_cm, leaf}}``."""
    for line in stdout.splitlines():
        if not line.startswith("RAYCAST:"):
            continue
        out: dict = {}
        for entry in line[len("RAYCAST:"):].split("|"):
            if "=" not in entry:
                continue
            d, val = entry.split("=", 1)
            d = d.strip()
            if val == "open":
                out[d] = {"hit": False}
            elif val.startswith("hit@"):
                rest = val[len("hit@"):]
                dist_str, _, leaf = rest.partition("cm:")
                try:
                    out[d] = {"hit": True, "dist_cm": float(dist_str),
                              "leaf": leaf.strip() or None}
                except ValueError:
                    pass
        return out or None
    return None


def format_raycast_block(parsed: Optional[dict],
                         length_cm: float = DEFAULT_RAY_LENGTH_CM) -> str:
    """Render a parsed raycast dict as a human-readable block for the prompt."""
    if not parsed:
        return f"  (no raycast — object missing or trace failed)"
    LABEL = {"+X": "+X", "-X": "-X", "+Y": "+Y", "-Y": "-Y",
             "+Z": "+Z (up)", "-Z": "-Z (down)"}
    lines = []
    for d in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
        v = parsed.get(d)
        if v is None:
            lines.append(f"  {LABEL[d]:<10s}  (missing)")
        elif not v.get("hit"):
            lines.append(f"  {LABEL[d]:<10s}  open (>{length_cm:.0f}cm)")
        else:
            leaf = v.get("leaf") or "?"
            lines.append(f"  {LABEL[d]:<10s}  hit @ {v['dist_cm']:.0f}cm  [{leaf}]")
    return "\n".join(lines)


def find_objects_by_alias_field(alias_field: str, aliases: dict) -> list[str]:
    """Resolve a task's `object_alias` field (which may be `"A | B | C"`)
    into a list of lowercased mesh leaves to look up. Returns one entry
    per alternative."""
    out: list[str] = []
    for piece in alias_field.split("|"):
        name = piece.strip()
        if not name:
            continue
        info = aliases["aliases"].get(name)
        if info is None:
            log.warning("alias %r not in aliases.json — skipping", name)
            continue
        out.append(info["real"].lower())
    return out


def resolve_candidate_aliases(spec: dict, aliases: dict) -> list[str]:
    """Resolve a task's `candidate_aliases` (a list of alias names) into
    lowercased mesh leaves. Falls back to splitting `object_alias` if
    `candidate_aliases` is missing — older tasks that only listed the
    moved object continue to work without modification.

    `candidate_aliases` exists so a task can declare the full set of
    objects whose initial poses should appear in the prompt without
    correspondence — even when only ONE of them is the alias-resolved
    actor the harness will move. E.g., the pan task lists both pan AND
    pot (the agent must pick which is which from the screenshot)."""
    aliases_list = spec.get("candidate_aliases")
    if aliases_list:
        out: list[str] = []
        for name in aliases_list:
            info = aliases["aliases"].get(name)
            if info is None:
                log.warning("alias %r not in aliases.json — skipping", name)
                continue
            out.append(info["real"].lower())
        return out
    return find_objects_by_alias_field(spec["object_alias"], aliases)


def query_object_poses_code(mesh_leaves_lower: list[str]) -> str:
    """UE script: find every actor whose mesh leaf matches one of
    `mesh_leaves_lower` (case-insensitive). Print one
    `OBJ_POSE <leaf> <X> <Y> <Z> <Pitch> <Yaw> <Roll>` line per hit
    plus a final `OBJ_POSE_DONE`. Useful to dump the starting pose of
    every candidate actor for multi-alias tasks (e.g., the mug variants)."""
    targets_repr = ", ".join(repr(l) for l in mesh_leaves_lower)
    return f"""\
import unreal_runtime as ur
TARGETS = {{{targets_repr}}}

def mesh_leaf(smc):
    mesh = None
    try: mesh = smc.StaticMesh
    except Exception: pass
    if mesh is None:
        try: mesh = smc.GetStaticMesh()
        except Exception: return None
    if mesh is None: return None
    return str(mesh).strip("<>").rsplit(".", 1)[-1].rsplit("/", 1)[-1].lower()

world = ur.Engine.GetDefaultWorld()
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        c = a.StaticMeshComponent
        if not c: continue
        leaf = mesh_leaf(c)
        if leaf in TARGETS:
            L = a.K2_GetActorLocation()
            R = a.K2_GetActorRotation()
            print(f"OBJ_POSE {{leaf}} {{L.X:.2f}} {{L.Y:.2f}} {{L.Z:.2f}} "
                  f"{{R.Pitch:.2f}} {{R.Yaw:.2f}} {{R.Roll:.2f}}")
    except Exception:
        continue
print("OBJ_POSE_DONE")
"""


def parse_object_poses(stdout: str) -> dict:
    """Parse `OBJ_POSE <leaf> ...` lines into ``{leaf: {X,Y,Z,Pitch,Yaw,Roll}}``."""
    out: dict = {}
    for line in stdout.splitlines():
        if not line.startswith("OBJ_POSE "):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        leaf = parts[1]
        try:
            x, y, z, p, yaw, r = (float(v) for v in parts[2:8])
        except ValueError:
            continue
        out[leaf] = {"X": x, "Y": y, "Z": z, "Pitch": p, "Yaw": yaw, "Roll": r}
    return out


QUERY_MARKER_COLORS: dict = {
    # Aligned with _GRID_PALETTE (further down in this file) so the
    # camera-grid round can hand colours straight to the agent and the
    # agent can name them back when proposing new ball coords.
    "red":     (1.00, 0.10, 0.10),
    "orange":  (1.00, 0.55, 0.00),
    "yellow":  (1.00, 0.95, 0.10),
    "lime":    (0.55, 1.00, 0.10),
    "green":   (0.10, 0.85, 0.20),
    "cyan":    (0.10, 1.00, 1.00),
    "blue":    (0.20, 0.40, 1.00),
    "purple":  (0.65, 0.20, 1.00),
    "magenta": (1.00, 0.20, 0.85),
    "white":   (1.00, 1.00, 1.00),
}


def spawn_query_markers_code(queries: list[dict]) -> str:
    """Spawn one tinted sphere per query at the requested world coord.
    Color does the visual identification; no text label is rendered (the
    agent uses the color value to map back to the world coord they
    emitted). Persists until cleared via :func:`clear_query_markers_code`.

    Each query is ``{"x": float, "y": float, "z": float, "color": str,
    "label"?: str}`` (color name from :data:`QUERY_MARKER_COLORS`,
    falls back to white if unknown; ``label`` is optional metadata for
    the agent's own bookkeeping and is NOT rendered into the scene).

    Actor labels follow ``BBoxQuery_<color>_<i>`` so cleanup can find them.
    """
    if not queries:
        return "print('NO_QUERIES')"
    safe = []
    for i, q in enumerate(queries):
        x = float(q["x"]); y = float(q["y"]); z = float(q["z"])
        c = q.get("color", "white").lower()
        rgb = QUERY_MARKER_COLORS.get(c, QUERY_MARKER_COLORS["white"])
        safe.append({"i": i, "x": x, "y": y, "z": z,
                     "r": rgb[0], "g": rgb[1], "b": rgb[2], "color_name": c})
    return f"""\
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
QUERIES = {json.dumps(safe)}

sphere = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
base_mat = ur.StaticLoadObject(
    ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")

for q in QUERIES:
    # Sphere marker at the world coord. Sphere asset is 100cm diameter
    # at scale 1, so scale 0.08 -> 8cm diameter (~4cm radius). Big
    # enough to be unambiguously visible at the camera angles we set
    # up, small enough not to occlude what's underneath.
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X=q["x"]; v.Y=q["y"]; v.Z=q["z"]
    t.Translation = v
    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(0, 0, 0))
    sv = ur.CoreUObject.Vector(); sv.X=0.08; sv.Y=0.08; sv.Z=0.08
    t.Scale3D = sv
    sph = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    try:
        sph.SetActorLabel(f"BBoxQuery_{{q['color_name']}}_{{q['i']}}")
    except Exception:
        pass
    smc = sph.StaticMeshComponent
    smc.SetMobility(2)
    smc.SetStaticMesh(sphere)
    smc.SetMaterial(0, base_mat)
    mid = smc.CreateDynamicMaterialInstance(0)
    col = ur.CoreUObject.LinearColor()
    col.R=q["r"]; col.G=q["g"]; col.B=q["b"]; col.A=1.0
    mid.SetVectorParameterValue("Color", col)
    smc.SetCollisionEnabled(0)
print(f"QUERY_MARKERS_OK n={{len(QUERIES)}}")
"""


def spawn_anchored_markers_code(queries: list[dict],
                                 axis_length_cm: float = 12.0) -> str:
    """Like :func:`spawn_query_markers_code` but each ball also gets:

    1. A camera-facing numeric label (the query's ``i`` index) above
       the sphere, with text colour = thresholded RGB inverse of the
       ball colour for max contrast.
    2. Three world-axis arrow bars emanating from the ball center:
       red = +X (Roll axis), green = +Y (Pitch axis), blue = +Z (Yaw
       axis). Each is a thin scaled cube ``axis_length_cm`` long.
    3. Tiny ``"X" / "Y" / "Z"`` text labels at each arrow tip,
       coloured to match the arrow.

    Used by harnesses that need the agent to see, every round, how the
    world XYZ axes map to screen direction at the current camera angle —
    without that visual reference the model can't ground its world-coord
    proposals against pixel evidence in just a few iterations.

    Camera pose is queried at runtime (pawn → controller) so labels
    rotate to face whatever camera the harness has set up.

    Each query is ``{"x", "y", "z", "color"}`` plus optional ``"i"`` (a
    stable per-ball index, otherwise we use list position).
    """
    if not queries:
        return "print('NO_QUERIES')"
    safe = []
    for i, q in enumerate(queries):
        x = float(q["x"]); y = float(q["y"]); z = float(q["z"])
        c = q.get("color", "white").lower()
        rgb = QUERY_MARKER_COLORS.get(c, QUERY_MARKER_COLORS["white"])
        idx = int(q.get("i", i))
        safe.append({"i": idx, "x": x, "y": y, "z": z,
                     "r": rgb[0], "g": rgb[1], "b": rgb[2], "color_name": c})
    return f"""\
import unreal_runtime as ur, math
QUERIES = {json.dumps(safe)}
AXIS_LEN = {axis_length_cm}

world = ur.Engine.GetDefaultWorld()
gm = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if not pawns:
    print("ANCHORED_NO_PAWN"); raise SystemExit
pawn = pawns[0]
cam_loc = pawn.K2_GetActorLocation()
cam_rot = pawn.GetController().GetControlRotation()
yaw_deg = cam_rot.Yaw
pitch_deg = cam_rot.Pitch
if pitch_deg > 180.0:
    pitch_deg -= 360.0

# Compute label "look at camera" rotator once. TextRender renders text
# in its local +X face, so an actor with yaw = cam_yaw + 180 has its
# +X pointing back at the camera (= readable). Pitch matches the
# camera's pitch (negated) so labels tilt up to face down-pointing
# cameras squarely.
label_yaw = yaw_deg + 180.0
label_pitch = -pitch_deg
kml = ur.Engine.KismetMathLibrary
label_rot = kml.Conv_RotatorToQuaternion(
    ur.CoreUObject.Rotator(label_pitch, label_yaw, 0))

sphere_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Sphere.Sphere")
cube_mesh = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(
    "/Engine/BasicShapes/Cube.Cube")
base_mat = ur.StaticLoadObject(
    ur.Engine.MaterialInterface, None,
    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")

# Cube default size is 100x100x100 cm at scale 1, so scaling 0.01
# gives 1 cm thickness. axis_len/100 along the active axis gives the
# requested length.
THIN = 0.010   # arrow bar thickness (1 cm cross-section)
LONG = AXIS_LEN / 100.0   # full length scale on the bar's axis
TIP_OFFSET = AXIS_LEN + 3.0   # tip label sits a bit past the bar

axis_specs = [
    # (label, color RGB, scale Vector, bar-center offset, tip-text offset)
    # The bar runs from the ball centre to +AXIS_LEN along its own axis;
    # the tip label sits a few cm past the bar end so the label is at
    # the head, marking the POSITIVE direction explicitly.
    ("+X", (1.0, 0.05, 0.05),
     (LONG, THIN, THIN),
     (AXIS_LEN / 2.0, 0.0, 0.0),
     (TIP_OFFSET, 0.0, 0.0)),
    ("+Y", (0.05, 1.0, 0.10),
     (THIN, LONG, THIN),
     (0.0, AXIS_LEN / 2.0, 0.0),
     (0.0, TIP_OFFSET, 0.0)),
    ("+Z", (0.10, 0.30, 1.0),
     (THIN, THIN, LONG),
     (0.0, 0.0, AXIS_LEN / 2.0),
     (0.0, 0.0, TIP_OFFSET)),
]

def make_static_mesh_actor(mesh, x, y, z, sx, sy, sz, label,
                           rgb, rotation_quat=None):
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X=x; v.Y=y; v.Z=z
    t.Translation = v
    t.Rotation = rotation_quat or kml.Conv_RotatorToQuaternion(
        ur.CoreUObject.Rotator(0, 0, 0))
    sv = ur.CoreUObject.Vector(); sv.X=sx; sv.Y=sy; sv.Z=sz
    t.Scale3D = sv
    a = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)
    try:
        a.SetActorLabel(label)
    except Exception:
        pass
    smc = a.StaticMeshComponent
    smc.SetMobility(2)
    smc.SetStaticMesh(mesh)
    smc.SetMaterial(0, base_mat)
    mid = smc.CreateDynamicMaterialInstance(0)
    col = ur.CoreUObject.LinearColor()
    col.R=rgb[0]; col.G=rgb[1]; col.B=rgb[2]; col.A=1.0
    mid.SetVectorParameterValue("Color", col)
    smc.SetCollisionEnabled(0)
    return a

def make_text_label(x, y, z, text, rgb_int, world_size, actor_label):
    t = ur.CoreUObject.Transform()
    v = ur.CoreUObject.Vector(); v.X=x; v.Y=y; v.Z=z
    t.Translation = v
    t.Rotation = label_rot
    t.Scale3D = ur.CoreUObject.Vector(1, 1, 1)
    lbl = world.SpawnActorEx(ur.Engine.TextRenderActor, t, 1)
    if not lbl:
        return None
    try:
        lbl.SetActorLabel(actor_label)
    except Exception:
        pass
    tr = lbl.TextRender
    tr.K2_SetText(text)
    tr.WorldSize = world_size
    tr.SetTextRenderColor(ur.CoreUObject.Color(
        rgb_int[0], rgb_int[1], rgb_int[2], 255))
    tr.HorizontalAlignment = ur.Engine.EHorizTextAligment.EHTA_Center
    tr.VerticalAlignment = ur.Engine.EVerticalTextAligment.EVRTA_TextCenter
    return lbl

for q in QUERIES:
    bx, by, bz = q["x"], q["y"], q["z"]
    r, g, b = q["r"], q["g"], q["b"]

    # 1) Sphere marker.
    make_static_mesh_actor(
        sphere_mesh, bx, by, bz, 0.08, 0.08, 0.08,
        f"BBoxQuery_{{q['color_name']}}_{{q['i']}}", (r, g, b))

    # Distance from camera (used to size labels into a roughly constant
    # screen fraction).
    dx = bx - cam_loc.X; dy = by - cam_loc.Y; dz = bz - cam_loc.Z
    dist = math.sqrt(dx*dx + dy*dy + dz*dz) or 1.0

    # 2) Numeric index label, offset purely along the ball→camera ray
    #    so it stays in the empty space between ball and viewer. Adding
    #    a +Z bias pushed labels for back-wall balls (close to camera,
    #    near the screen edge) outside the FOV — keep the offset
    #    radial to camera direction so the label always projects
    #    inward toward optical axis.
    to_cam_x = (cam_loc.X - bx) / dist
    to_cam_y = (cam_loc.Y - by) / dist
    to_cam_z = (cam_loc.Z - bz) / dist
    OFF = 12.0
    lx = bx + to_cam_x * OFF
    ly = by + to_cam_y * OFF
    lz = bz + to_cam_z * OFF
    inv_r = 255 if r < 0.5 else 0
    inv_g = 255 if g < 0.5 else 0
    inv_b = 255 if b < 0.5 else 0
    HFOV_RAD = math.radians(90.0)
    VFOV_RAD = 2.0 * math.atan(math.tan(HFOV_RAD / 2.0) / (4.0 / 3.0))
    main_size = max(6.0, 0.12 * dist * math.tan(VFOV_RAD / 2.0))
    make_text_label(
        lx, ly, lz, str(q["i"]),
        (inv_r, inv_g, inv_b), main_size,
        f"BBoxQuery_balllbl_{{q['i']}}")

    # 3) + 4) Three axis arrows + tip labels.
    for axis_name, axis_rgb, scale, center, tip in axis_specs:
        cx = bx + center[0]; cy = by + center[1]; cz = bz + center[2]
        make_static_mesh_actor(
            cube_mesh, cx, cy, cz, scale[0], scale[1], scale[2],
            f"BBoxQuery_axis{{axis_name}}_{{q['i']}}",
            axis_rgb)
        # Tiny axis-tip label.
        tip_size = max(3.0, 0.06 * dist * math.tan(VFOV_RAD / 2.0))
        tx = bx + tip[0]; ty = by + tip[1]; tz = bz + tip[2]
        ar, ag, ab = axis_rgb
        ari = int(round(ar * 255)); agi = int(round(ag * 255)); abi = int(round(ab * 255))
        make_text_label(
            tx, ty, tz, axis_name,
            (ari, agi, abi), tip_size,
            f"BBoxQuery_axis{{axis_name}}lbl_{{q['i']}}")

print(f"ANCHORED_MARKERS_OK n={{len(QUERIES)}}")
"""


def clear_query_markers_code() -> str:
    """Destroy every actor whose label starts with ``BBoxQuery_``. Called
    at episode start so previous-run leftovers don't show up in the first
    screenshot. Loops over both StaticMeshActor (current sphere markers)
    and TextRenderActor (legacy from the text+sphere variant — keeps the
    cleanup pass safe across schema migrations)."""
    return """\
import unreal_runtime as ur
world = ur.Engine.GetDefaultWorld()
n = 0
for cls in (ur.Engine.StaticMeshActor, ur.Engine.TextRenderActor):
    for a in world.GetActorsOfClass(cls):
        try:
            if a.GetActorLabel().startswith("BBoxQuery_"):
                a.K2_DestroyActor(); n += 1
        except Exception:
            pass
print(f"QUERY_MARKERS_CLEARED n={n}")
"""


def camera_axis_raycast_code(_unused: object = None, max_dist_cm: float = 2000.0) -> str:
    """Cast a ray from the player pawn (= camera position for our
    DefaultPawn-based scenes) along its current control rotation and
    print the hit world coord. Used by harness_iterate to give the agent
    a concrete anchor for "what's the camera looking at"; without this
    the model has only the camera pose and tends to drop balls outside
    the FOV.

    Pattern follows ``navigation/debug/04b_test_raycast_safe.py``:
    LineTraceSingle takes ``(start, end, channel, complex, ignore,
    debug, hit, ignoreself, traceColor, hitColor, drawTime)`` — note
    drawTime must be > 0 even for ``ForOneFrame``, and ``HitResult``
    exposes ``ImpactPoint`` / ``Distance``, not ``Location``.

    The DefaultPawn used in the apartment scene has the camera at the
    actor root (no SpringArm / eye-height offset), so
    ``pawn.K2_GetActorLocation()`` is the screenshot's view point.
    ``GameplayStatics.GetPlayerCameraManager`` is NOT bound on this
    build's unreal_runtime — calling it crashes UE.

    The first arg is ignored (legacy callers passed pawn_pose dict).

    Output format:  ``CAMERA_AXIS_START <X> <Y> <Z> yaw=<deg> pitch=<deg>``
                    then ``CAMERA_AXIS_HIT <X> <Y> <Z>`` (one line)
    or              ``CAMERA_AXIS_NO_HIT`` if the ray misses.
    """
    return f"""\
import unreal_runtime as ur, math
world = ur.Engine.GetDefaultWorld()
gm = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if not pawns:
    print("CAMERA_AXIS_NO_PAWN"); raise SystemExit
pawn = pawns[0]
loc = pawn.K2_GetActorLocation()
rot = pawn.GetController().GetControlRotation()
# Fold pitch from UE's [0, 360) wrap into [-180, 180] so the trig works
# for "looking down" (e.g. raw 311.9° = -48.1°).
pitch_deg = rot.Pitch
if pitch_deg > 180.0:
    pitch_deg -= 360.0
print(f"CAMERA_AXIS_START {{loc.X:.2f}} {{loc.Y:.2f}} {{loc.Z:.2f}} "
      f"yaw={{rot.Yaw:.2f}} pitch={{pitch_deg:.2f}}")
yaw = math.radians(rot.Yaw); pitch = math.radians(pitch_deg)
fx = math.cos(yaw) * math.cos(pitch)
fy = math.sin(yaw) * math.cos(pitch)
fz = math.sin(pitch)
ksm = ur.Engine.KismetSystemLibrary
start = ur.CoreUObject.Vector(); start.X=loc.X; start.Y=loc.Y; start.Z=loc.Z
end = ur.CoreUObject.Vector()
end.X = loc.X + fx * {max_dist_cm}
end.Y = loc.Y + fy * {max_dist_cm}
end.Z = loc.Z + fz * {max_dist_cm}
out_hit = ur.Engine.HitResult()
tc = ur.CoreUObject.LinearColor(); tc.R=1; tc.G=0; tc.B=0; tc.A=1
hc = ur.CoreUObject.LinearColor(); hc.R=0; hc.G=1; hc.B=0; hc.A=1
got = ksm.LineTraceSingle(
    start, end,
    ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
    False, [],
    ur.Engine.EDrawDebugTrace.ForOneFrame,
    out_hit, True, tc, hc, 1.0)
if got and out_hit.bBlockingHit:
    ip = out_hit.ImpactPoint
    print(f"CAMERA_AXIS_HIT {{ip.X:.2f}} {{ip.Y:.2f}} {{ip.Z:.2f}} dist={{out_hit.Distance:.1f}}")
else:
    print("CAMERA_AXIS_NO_HIT")
"""


def parse_grid_raycast(stdout: str) -> list[dict]:
    """Parse ``GRID_RAY idx=<n> i=<col> j=<row> color=<name> hit=<X> <Y> <Z>
    dist=<cm>`` lines from :func:`camera_grid_raycast_code` into a sorted
    list of ``{idx, i, j, color, X, Y, Z, distance_cm}`` dicts. ``NO_HIT``
    rays are dropped (the harness never references them)."""
    out: list[dict] = []
    for line in stdout.splitlines():
        if not line.startswith("GRID_RAY "):
            continue
        if " NO_HIT" in line:
            continue
        kv = {}
        rest = line[len("GRID_RAY "):]
        # Fields are space-separated key=value, except `hit=` is followed
        # by 3 free-floating numbers (X Y Z) before the next key=value.
        toks = rest.split()
        i = 0
        while i < len(toks):
            tok = toks[i]
            if tok.startswith("hit="):
                kv["X"] = float(tok[len("hit="):])
                kv["Y"] = float(toks[i + 1])
                kv["Z"] = float(toks[i + 2])
                i += 3
                continue
            if "=" in tok:
                k, v = tok.split("=", 1)
                kv[k] = v
            i += 1
        try:
            entry = {
                "idx": int(kv["idx"]),
                "i": int(kv["i"]),
                "j": int(kv["j"]),
                "color": kv.get("color", ""),
                "X": kv["X"], "Y": kv["Y"], "Z": kv["Z"],
                "distance_cm": float(kv.get("dist", "0") or 0),
            }
        except (KeyError, ValueError):
            continue
        out.append(entry)
    out.sort(key=lambda e: e["idx"])
    return out


def parse_camera_axis_hit(stdout: str) -> Optional[dict]:
    """Parse the ``CAMERA_AXIS_HIT`` output from
    :func:`camera_axis_raycast_code` into
    ``{X, Y, Z, distance_cm?}``."""
    for line in stdout.splitlines():
        if not line.startswith("CAMERA_AXIS_HIT "):
            continue
        parts = line[len("CAMERA_AXIS_HIT "):].split()
        if len(parts) < 3:
            return None
        try:
            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            return None
        out: dict = {"X": x, "Y": y, "Z": z}
        for tok in parts[3:]:
            if tok.startswith("dist="):
                try:
                    out["distance_cm"] = float(tok[len("dist="):])
                except ValueError:
                    pass
        return out
    return None


_GRID_PALETTE: list = [
    # 9 visually distinct hand-picked colours for the 3×3 case (extend
    # if grid_n > 3 — the helper falls back to HSV cycling past index 8).
    ("red",     (1.00, 0.10, 0.10)),
    ("orange",  (1.00, 0.55, 0.00)),
    ("yellow",  (1.00, 0.95, 0.10)),
    ("lime",    (0.55, 1.00, 0.10)),
    ("green",   (0.10, 0.85, 0.20)),
    ("cyan",    (0.10, 1.00, 1.00)),
    ("blue",    (0.20, 0.40, 1.00)),
    ("purple",  (0.65, 0.20, 1.00)),
    ("magenta", (1.00, 0.20, 0.85)),
]


def camera_grid_raycast_code(grid_n: int = 3, fov_fraction: float = 0.7,
                              max_dist_cm: float = 2000.0,
                              hfov_deg: float = 90.0,
                              aspect: float = 4.0 / 3.0) -> str:
    """Fire a ``grid_n × grid_n`` grid of rays from the pawn (= camera)
    along its control rotation, evenly spaced across ``fov_fraction`` of
    the camera's horizontal/vertical FOV, and PRINT the hits. Does NOT
    spawn anything in the scene — callers should parse the output via
    :func:`parse_grid_raycast` and pass to :func:`spawn_anchored_markers_code`
    if visual markers are wanted, so all on-screen balls (round-1 grid
    + later-round agent balls) share one consistent visualisation.

    Uses the verified raycast pattern from
    ``navigation/debug/04b_test_raycast_safe.py`` (no
    GetPlayerCameraManager — that crashes this build).

    FOV: this build's runtime doesn't expose the camera-manager FOV via
    Python, so we accept it as a parameter. UE FPS-style DefaultPawn
    defaults to 90° horizontal — override if your build is different.

    Indexing: ``idx = j * grid_n + i`` so labels read row-by-row from
    the top-left of the screen (i = column 0=left, j = row 0=top).

    Output (one line per ray):
        ``GRID_RAY idx=<n> i=<col> j=<row> color=<name> hit=<X> <Y> <Z> dist=<cm>``
    or  ``GRID_RAY idx=<n> i=<col> j=<row> NO_HIT``

    plus a header:
        ``GRID_CAM start=<X> <Y> <Z> yaw=<deg> pitch=<deg> hfov=<deg> vfov=<deg>``
    """
    palette_json = json.dumps(_GRID_PALETTE)
    return f"""\
import unreal_runtime as ur, math, colorsys
GRID_N = {grid_n}
FOV_FRAC = {fov_fraction}
MAX_DIST = {max_dist_cm}
HFOV_DEG = {hfov_deg}
ASPECT = {aspect}
PALETTE = {palette_json}

world = ur.Engine.GetDefaultWorld()
gm = ur.Engine.GetGameMode()
pawns = world.GetActorsOfClass(gm.DefaultPawnClass)
if not pawns:
    print("GRID_NO_PAWN"); raise SystemExit
pawn = pawns[0]
loc = pawn.K2_GetActorLocation()
rot = pawn.GetController().GetControlRotation()
pitch_deg = rot.Pitch
if pitch_deg > 180.0:
    pitch_deg -= 360.0
yaw_deg = rot.Yaw

hfov_rad = math.radians(HFOV_DEG)
vfov_rad = 2.0 * math.atan(math.tan(hfov_rad / 2.0) / ASPECT)
vfov_deg = math.degrees(vfov_rad)
print(f"GRID_CAM start={{loc.X:.2f}} {{loc.Y:.2f}} {{loc.Z:.2f}} "
      f"yaw={{yaw_deg:.2f}} pitch={{pitch_deg:.2f}} "
      f"hfov={{HFOV_DEG:.2f}} vfov={{vfov_deg:.2f}}")

base_yaw = math.radians(yaw_deg)
base_pitch = math.radians(pitch_deg)
fx0 = math.cos(base_yaw) * math.cos(base_pitch)
fy0 = math.sin(base_yaw) * math.cos(base_pitch)
fz0 = math.sin(base_pitch)
rx = -math.sin(base_yaw); ry =  math.cos(base_yaw); rz = 0.0
ux = ry * fz0 - rz * fy0
uy = rz * fx0 - rx * fz0
uz = rx * fy0 - ry * fx0

htan = math.tan(hfov_rad / 2.0) * FOV_FRAC
vtan = math.tan(vfov_rad / 2.0) * FOV_FRAC

ksm = ur.Engine.KismetSystemLibrary

def grid_color(idx):
    if idx < len(PALETTE):
        name, rgb = PALETTE[idx]
        return name, rgb[0], rgb[1], rgb[2]
    h = (idx - len(PALETTE)) % 12 / 12.0
    r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
    return f"hsv_{{idx}}", r, g, b

n_hit = 0
n_miss = 0
for j in range(GRID_N):
    for i in range(GRID_N):
        if GRID_N == 1:
            sx = sy = 0.0
        else:
            sx = (2.0 * i / (GRID_N - 1) - 1.0) * htan
            sy = (2.0 * j / (GRID_N - 1) - 1.0) * vtan
        dx = fx0 + sx * rx + sy * ux
        dy = fy0 + sx * ry + sy * uy
        dz = fz0 + sx * rz + sy * uz
        norm = math.sqrt(dx*dx + dy*dy + dz*dz)
        dx /= norm; dy /= norm; dz /= norm

        start = ur.CoreUObject.Vector()
        start.X = loc.X; start.Y = loc.Y; start.Z = loc.Z
        end = ur.CoreUObject.Vector()
        end.X = loc.X + dx * MAX_DIST
        end.Y = loc.Y + dy * MAX_DIST
        end.Z = loc.Z + dz * MAX_DIST
        out_hit = ur.Engine.HitResult()
        tc = ur.CoreUObject.LinearColor(); tc.R=1; tc.G=1; tc.B=0; tc.A=1
        hc = ur.CoreUObject.LinearColor(); hc.R=0; hc.G=1; hc.B=0; hc.A=1
        got = ksm.LineTraceSingle(
            start, end,
            ur.Engine.ETraceTypeQuery.TraceTypeQuery1,
            False, [],
            ur.Engine.EDrawDebugTrace.ForOneFrame,
            out_hit, True, tc, hc, 1.0)
        idx = j * GRID_N + i  # row-major: top-left → bottom-right
        color_name, r, g, b = grid_color(idx)
        if got and out_hit.bBlockingHit:
            ip = out_hit.ImpactPoint
            print(f"GRID_RAY idx={{idx}} i={{i}} j={{j}} color={{color_name}} "
                  f"hit={{ip.X:.2f}} {{ip.Y:.2f}} {{ip.Z:.2f}} "
                  f"dist={{out_hit.Distance:.1f}}")
            n_hit += 1
        else:
            print(f"GRID_RAY idx={{idx}} i={{i}} j={{j}} NO_HIT")
            n_miss += 1
print(f"GRID_DONE hits={{n_hit}} misses={{n_miss}}")
"""


def get_object_pose_code(mesh_leaf_lower: str) -> str:
    return f"""\
import unreal_runtime as ur
TARGET = "{mesh_leaf_lower}"

def mesh_leaf(smc):
    mesh = None
    try: mesh = smc.StaticMesh
    except Exception: pass
    if mesh is None:
        try: mesh = smc.GetStaticMesh()
        except Exception: return None
    if mesh is None: return None
    return str(mesh).strip("<>").rsplit(".", 1)[-1].rsplit("/", 1)[-1].lower()

world = ur.Engine.GetDefaultWorld()
found = False
for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):
    try:
        c = a.StaticMeshComponent
        if c and mesh_leaf(c) == TARGET:
            L = a.K2_GetActorLocation()
            R = a.K2_GetActorRotation()
            print(f"OBJ_POSE {{L.X:.2f}} {{L.Y:.2f}} {{L.Z:.2f}} "
                  f"{{R.Pitch:.2f}} {{R.Yaw:.2f}} {{R.Roll:.2f}}")
            found = True
            break
    except Exception:
        continue
if not found:
    print("OBJ_POSE_NOT_FOUND")
"""


# ---------------------------------------------------------------------------
# Stdout helpers
# ---------------------------------------------------------------------------
def unwrap(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


_POSE_RE = re.compile(
    r"(?:OBJ_FOUND\s+label=\S+\s+|OBJ_POSE\s+|MOVE_OK\s+)"
    r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+"
    r"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"
)


def parse_pose_line(stdout: str) -> Optional[dict]:
    for line in stdout.splitlines():
        m = _POSE_RE.search(line)
        if m:
            x, y, z, p, yaw, r = m.groups()
            return {"X": float(x), "Y": float(y), "Z": float(z),
                    "Pitch": float(p), "Yaw": float(yaw), "Roll": float(r)}
    return None


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------
def _angle_delta_deg(a: float, b: float) -> float:
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def verify_pose(spec: dict, pose: dict,
                initial_pose: Optional[dict] = None) -> dict:
    """Return `{"passed", "reason", "loc_distance", "rot_deltas"}`.

    ``target_rotation`` may now declare a SUBSET of axes (e.g. just
    ``{Pitch, Roll}``); only the listed axes are checked. ``target_rotation_relative``
    is an optional sibling field whose ``yaw_delta`` (degrees) sets the
    required final yaw relative to ``initial_pose["Yaw"]``. Used by
    handle-orientation tasks (pan, knife) — pass the actor's pre-task
    pose as ``initial_pose`` so the verifier can compute it.
    """
    out: dict = {"passed": False, "reason": "", "loc_distance": None,
                 "rot_deltas": None}
    tol = float(spec.get("tolerance_cm", 5.0))
    if "target_location" in spec:
        t = spec["target_location"]
        d = math.sqrt((pose["X"] - t["X"]) ** 2 +
                      (pose["Y"] - t["Y"]) ** 2 +
                      (pose["Z"] - t["Z"]) ** 2)
        out["loc_distance"] = round(d, 2)
        loc_ok = d <= tol
        if not loc_ok:
            out["reason"] = f"location {d:.2f}cm > tol {tol}cm"
    elif "target_location_range" in spec:
        r = spec["target_location_range"]
        loc_ok = (r["X"][0] <= pose["X"] <= r["X"][1] and
                  r["Y"][0] <= pose["Y"] <= r["Y"][1] and
                  r["Z"][0] <= pose["Z"] <= r["Z"][1])
        if not loc_ok:
            out["reason"] = (f"location ({pose['X']:.1f},{pose['Y']:.1f},"
                             f"{pose['Z']:.1f}) outside range {r}")
    else:
        out["reason"] = "spec has no target_location or target_location_range"
        return out

    rot_ok = True
    rtol = float(spec.get("rotation_tolerance_deg", 5.0))
    rot_deltas: dict = {}
    if "target_rotation" in spec:
        rt = spec["target_rotation"]
        for k in ("Pitch", "Yaw", "Roll"):
            if k not in rt:
                continue
            rot_deltas[k] = round(_angle_delta_deg(pose[k], rt[k]), 2)
        if rot_deltas and any(v > rtol for v in rot_deltas.values()):
            rot_ok = False
            if not out["reason"]:
                out["reason"] = (f"rotation deltas {rot_deltas} exceed "
                                 f"tol {rtol}deg")
    if "target_rotation_relative" in spec:
        rel = spec["target_rotation_relative"]
        if initial_pose is None:
            rot_ok = False
            if not out["reason"]:
                out["reason"] = ("target_rotation_relative present but "
                                 "initial_pose not passed to verify_pose")
        elif "yaw_delta" in rel:
            expected_yaw = (float(initial_pose["Yaw"]) +
                            float(rel["yaw_delta"])) % 360.0
            yaw_delta_obs = round(_angle_delta_deg(pose["Yaw"],
                                                    expected_yaw), 2)
            rot_deltas["Yaw_relative"] = yaw_delta_obs
            if yaw_delta_obs > rtol:
                rot_ok = False
                if not out["reason"]:
                    out["reason"] = (
                        f"yaw {pose['Yaw']:.1f} not within {rtol}deg of "
                        f"required {expected_yaw:.1f} (initial Yaw "
                        f"{initial_pose['Yaw']:.1f} + "
                        f"{rel['yaw_delta']:.1f} delta)"
                    )
    out["rot_deltas"] = rot_deltas if rot_deltas else None

    out["passed"] = bool(loc_ok and rot_ok)
    if out["passed"]:
        out["reason"] = "ok"
    return out


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------
PACKAGE_SCREENSHOT_DIR = Path(
    os.environ.get(
        "OBJECT_MOVE_SCREENSHOT_DIR",
        r"${UE_BUILD_ROOT}\PackagedOutput_v2\Windows\demo1\Saved\Screenshots\Windows",
    )
)


async def take_screenshot(engine, name: str, workspace: Path,
                          screenshot_dir: Optional[Path] = None) -> Path:
    from veriworld.common.screenshot import capture_runtime_screenshot
    return await capture_runtime_screenshot(
        engine, name, workspace, screenshot_dir=screenshot_dir)


# ---------------------------------------------------------------------------
# Response parser (same shape as light_war)
# ---------------------------------------------------------------------------
_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)
_JSON_BRACE = re.compile(r"\{[\s\S]*?\}")


def extract_action(text: str) -> Optional[dict]:
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    for m in _JSON_BRACE.finditer(text):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and "action" in d:
                return d
        except Exception:
            continue
    return None


def extract_thought(text: str) -> str:
    return text.split("```", 1)[0].strip()


__all__ = [
    "HERE", "APARTMENT_LEVEL", "PACKAGE_SCREENSHOT_DIR",
    "DEFAULT_RAY_LENGTH_CM", "QUERY_MARKER_COLORS",
    "load_groundtruth", "load_aliases", "resolve_alias", "resolve_camera_view",
    "find_objects_by_alias_field", "resolve_candidate_aliases",
    "OPEN_APARTMENT_CODE", "reset_pawn_code",
    "find_and_prep_object_code", "move_object_code", "get_object_pose_code",
    "query_object_poses_code", "parse_object_poses",
    "spawn_query_markers_code", "spawn_anchored_markers_code",
    "clear_query_markers_code",
    "camera_axis_raycast_code", "parse_camera_axis_hit",
    "camera_grid_raycast_code", "parse_grid_raycast",
    "build_raycast_code", "parse_raycast_line", "format_raycast_block",
    "unwrap", "parse_pose_line", "verify_pose",
    "take_screenshot", "extract_action", "extract_thought",
]
