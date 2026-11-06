"""Shared building blocks for place_on_platform harnesses.

* Level switch + scene cleanup
* Spawn boxes (cube primitive + MID color, NO RuntimeMaterialLibrary
  shader compile — that crashes PackagedOutput_v2)
* Apply structured agent action (probe / move_to / stop)
* Settle physics + verify cube on platform
* Stdout helpers and parsers
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import Optional

HERE = Path(__file__).parent
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Level switch (Untitled, like billiards / mazenavfps)
# ---------------------------------------------------------------------------
LEVEL_SWITCH_CODE = (
    "import unreal_runtime as ur\n"
    'cur = ur.Engine.GameplayStatics.GetCurrentLevelName(True)\n'
    'if cur != "Untitled":\n'
    '    ur.Engine.GameplayStatics.OpenLevel("Untitled", True, "")\n'
    '    print("LEVEL_SWITCHING")\n'
    'else:\n'
    '    print("LEVEL_READY")\n'
)


# ---------------------------------------------------------------------------
# Scene cleanup. Removes any actors with PLACE_* labels (prior episode) and
# any leftover "Cube" residue from a fresh OpenLevel call.
# ---------------------------------------------------------------------------
CLEAN_SCENE_CODE = (
    "import unreal_runtime as ur\n"
    "world = ur.Engine.GetDefaultWorld()\n"
    "removed = 0\n"
    "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
    "    lbl = a.GetActorLabel()\n"
    '    if lbl.startswith("PLACE_") or lbl == "Cube":\n'
    "        a.K2_DestroyActor()\n"
    "        removed += 1\n"
    'print(f"CLEANED {removed}")\n'
)


# ---------------------------------------------------------------------------
# Spawn the scene from a params dict. Embeds the JSON so a single
# python_exec packs the whole setup. Uses MID + "Color" parameter on
# /Engine/BasicShapes/BasicShapeMaterial (no shader compile needed).
# ---------------------------------------------------------------------------
def setup_scene_code(params: dict) -> str:
    p_json = json.dumps(params)
    return (
        "import unreal_runtime as ur\n"
        "import json, math\n"
        f"P = json.loads(r'''{p_json}''')\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "BASE_MAT = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,\n"
        '    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")\n'
        "CUBE_MESH = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(\n"
        '    "/Engine/BasicShapes/Cube.Cube")\n'
        "SPHERE_MESH = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(\n"
        '    "/Engine/BasicShapes/Sphere.Sphere")\n'
        "MESHES = {'cube': CUBE_MESH, 'sphere': SPHERE_MESH}\n"
        "\n"
        "def spawn(pos, scale, label, rgb, physics=False, mass=1.0, mesh='cube'):\n"
        "    t = ur.CoreUObject.Transform()\n"
        "    t.Translation = ur.CoreUObject.Vector(pos[0], pos[1], pos[2])\n"
        "    t.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(\n"
        "        ur.CoreUObject.Rotator(0, 0, 0))\n"
        "    sv = ur.CoreUObject.Vector(); sv.X, sv.Y, sv.Z = scale[0], scale[1], scale[2]\n"
        "    t.Scale3D = sv\n"
        "    actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, t, 1)\n"
        "    actor.SetActorLabel(label)\n"
        "    mc = actor.StaticMeshComponent\n"
        "    mc.SetMobility(2)\n"
        "    mc.SetStaticMesh(MESHES.get(mesh, CUBE_MESH))\n"
        "    if BASE_MAT:\n"
        "        mc.SetMaterial(0, BASE_MAT)\n"
        "        mid = mc.CreateDynamicMaterialInstance(0)\n"
        "        if mid:\n"
        "            col = ur.CoreUObject.LinearColor()\n"
        "            col.R, col.G, col.B, col.A = rgb[0], rgb[1], rgb[2], 1.0\n"
        '            mid.SetVectorParameterValue("Color", col)\n'
        "    if physics:\n"
        "        mc.SetCollisionEnabled(1)\n"
        "        mc.SetCollisionProfileName('PhysicsActor')\n"
        "        mc.SetSimulatePhysics(True)\n"
        "        mc.SetEnableGravity(True)\n"
        "        mc.SetLinearDamping(0.5)\n"
        "        mc.SetAngularDamping(1.0)\n"
        "        mc.SetMassOverrideInKg('', mass, True)\n"
        "    else:\n"
        "        mc.SetCollisionEnabled(1)\n"
        "        mc.SetCollisionProfileName('BlockAll')\n"
        "    return actor\n"
        "\n"
        "# Spawn one actor per OBJ slot.\n"
        "obj_order = P['obj_order']\n"
        "for i in range(P['n_total_objects']):\n"
        "    role = obj_order[i]\n"
        "    label = f'PLACE_OBJ_{i}'\n"
        "    if role == 0:\n"
        "        pl = P['platform']\n"
        "        s = [pl['dimensions'][0]/100.0, pl['dimensions'][1]/100.0, pl['dimensions'][2]/100.0]\n"
        "        spawn(pl['position'], s, label, pl['color'])\n"
        "    elif role == 1:\n"
        "        c = P['cube']\n"
        "        s = [c['side']/100.0]*3\n"
        "        spawn(c['start_position'], s, label, c['color'], physics=True, mass=c['mass'])\n"
        "    else:\n"
        "        d = P['distractors'][role - 2]\n"
        "        s = [d['side']/100.0]*3\n"
        "        spawn(d['position'], s, label, d['color'], mesh=d.get('mesh','cube'))\n"
        "\n"
        "# Ground plane (tinted dark grey).\n"
        "spawn([0, 0, -5], [50, 50, 0.1], 'PLACE_OBJ_ground', [0.30, 0.30, 0.30])\n"
        "\n"
        "# Camera — auto-frame the scene including ALL objects within\n"
        "# the camera's FOV. Center on the bounding-box center (not the\n"
        "# average), and pull the camera far enough back that the\n"
        "# largest XY half-diagonal fits within ~70% of the horizontal\n"
        "# FOV (~90deg default). Pitch tilted down 35deg so vertical\n"
        "# extent is comfortably framed. Without this, the cube\n"
        "# (which spawns ~400cm off to the side of the platform) was\n"
        "# clipped at the screen edge.\n"
        "objs = [P['cube']['start_position'], P['platform']['position']] + [d['position'] for d in P['distractors']]\n"
        "min_x = min(o[0] for o in objs); max_x = max(o[0] for o in objs)\n"
        "min_y = min(o[1] for o in objs); max_y = max(o[1] for o in objs)\n"
        "min_z = min(o[2] for o in objs); max_z = max(o[2] for o in objs)\n"
        "cx = (min_x + max_x) / 2.0; cy = (min_y + max_y) / 2.0\n"
        "extent_xy = max(max_x - min_x, max_y - min_y, 300.0)\n"
        "extent_with_pad = extent_xy + 200.0  # add box-half-diagonal margin\n"
        "# Need cam_dist such that half_extent fits in tan(half_fov*0.7).\n"
        "# UE default FOV = 90deg; effective half = 45deg; aim for 0.7x = ~32deg.\n"
        "import math as _m\n"
        "cam_dist = extent_with_pad / (2 * _m.tan(_m.radians(32.0)))\n"
        "cam_dist = max(cam_dist, 800.0)\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if pawns:\n"
        "    p = pawns[0]\n"
        "    # Place camera south-east of bbox center, looking NW-ish, tilted down 35deg.\n"
        "    cx2 = cx + cam_dist*0.6; cy2 = cy - cam_dist*0.5; cz2 = max_z + cam_dist*0.55\n"
        "    p.K2_SetActorLocation(ur.CoreUObject.Vector(cx2, cy2, cz2), False)\n"
        "    yaw = math.degrees(math.atan2(cy-cy2, cx-cx2))\n"
        "    r = ur.CoreUObject.Rotator(); r.Pitch=-35.0; r.Yaw=yaw; r.Roll=0.0\n"
        "    p.GetController().SetControlRotation(r)\n"
        "print(f'SCENE_READY n={P[\"n_total_objects\"]}')\n"
    )


# ---------------------------------------------------------------------------
# Read symbolic state of every PLACE_OBJ_i actor — generic info only
# (label, position, scale, rgb if discoverable). The agent uses this in
# the symbolic / combined harnesses to identify objects.
# ---------------------------------------------------------------------------
QUERY_STATE_CODE = (
    "import unreal_runtime as ur, json\n"
    "world = ur.Engine.GetDefaultWorld()\n"
    "out = []\n"
    "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
    "    lbl = a.GetActorLabel()\n"
    '    if not lbl.startswith("PLACE_OBJ_"): continue\n'
    "    if lbl.endswith('ground'): continue\n"
    "    loc = a.K2_GetActorLocation()\n"
    "    s = a.GetTransform().Scale3D\n"
    "    out.append({'label': lbl, 'position': [round(loc.X,1), round(loc.Y,1), round(loc.Z,1)], "
    "                'scale_cm': [round(s.X*100,1), round(s.Y*100,1), round(s.Z*100,1)]})\n"
    "print('STATE:'+json.dumps(out))\n"
)


def parse_state(stdout: str) -> list[dict]:
    for line in stdout.splitlines():
        if line.startswith("STATE:"):
            try:
                return json.loads(line[len("STATE:"):])
            except Exception:
                return []
    return []


# ---------------------------------------------------------------------------
# Symbolic state augmentation — annotate each entry with its rgb so the
# symbolic / combined harnesses don't have to ask the agent to "see" colors.
# We pull rgb from the params dict (deterministic per seed).
# ---------------------------------------------------------------------------
def annotate_with_colors(state: list[dict], params: dict) -> list[dict]:
    """Add ``rgb`` and ``mesh`` fields to each state entry by looking
    up the label's role via params['obj_order']. Cube + platform are
    always 'cube' mesh; distractors carry their per-seed mesh type."""
    obj_order = params["obj_order"]
    out = []
    for s in state:
        lbl = s["label"]
        try:
            i = int(lbl.split("_")[-1])
        except ValueError:
            continue
        if i >= len(obj_order):
            continue
        role = obj_order[i]
        if role == 0:
            rgb = params["platform"]["color"]
            mesh = "cube"
        elif role == 1:
            rgb = params["cube"]["color"]
            mesh = "cube"
        else:
            d = params["distractors"][role - 2]
            rgb = d["color"]
            mesh = d.get("mesh", "cube")
        out.append({**s, "rgb": rgb, "mesh": mesh})
    return out


# ---------------------------------------------------------------------------
# Action dispatch: probe / move_to / stop. Agent emits one JSON action per
# round; the harness translates it into a python_exec snippet.
# ---------------------------------------------------------------------------
def _move_label_code(label: str, dx: float, dy: float, dz: float,
                     absolute: bool = False) -> str:
    """Translate or teleport an actor by label. ``absolute=True`` means
    ``(dx, dy, dz)`` is the new world position; False means delta."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        f"target = '{label}'\n"
        "found = None\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    if a.GetActorLabel() == target:\n"
        "        found = a; break\n"
        "if not found:\n"
        "    print(f'NOT_FOUND {target}')\n"
        "else:\n"
        "    loc = found.K2_GetActorLocation()\n"
        f"    nx = ({dx}) if {absolute} else loc.X + ({dx})\n"
        f"    ny = ({dy}) if {absolute} else loc.Y + ({dy})\n"
        f"    nz = ({dz}) if {absolute} else loc.Z + ({dz})\n"
        "    v = ur.CoreUObject.Vector(); v.X, v.Y, v.Z = nx, ny, nz\n"
        "    found.K2_SetActorLocation(v, False)\n"
        # If it has physics, zero out its velocity so it doesn't fly off.
        "    mc = found.StaticMeshComponent\n"
        "    try:\n"
        "        zero = ur.CoreUObject.Vector(); zero.X=0; zero.Y=0; zero.Z=0\n"
        "        mc.SetPhysicsLinearVelocity(zero, False, '')\n"
        "        mc.SetPhysicsAngularVelocityInDegrees(zero, False, '')\n"
        "    except Exception:\n"
        "        pass\n"
        "    out = found.K2_GetActorLocation()\n"
        f"    print(f'MOVED {label} {{out.X:.1f}} {{out.Y:.1f}} {{out.Z:.1f}}')\n"
    )


def move_to_code(label: str, x: float, y: float, z: float) -> str:
    return _move_label_code(label, x, y, z, absolute=True)


def lift_code(label: str, dz: float) -> str:
    return _move_label_code(label, 0.0, 0.0, dz, absolute=False)


def probe_lift_code(label: str, dz: float = 200.0) -> str:
    """Lift an actor by ``dz`` for visual probing AND freeze its
    physics so gravity doesn't drag it back down during the
    screenshot delay. Prints the pre-lift position so the harness can
    restore exactly:

      ``PRE_POS <X> <Y> <Z>``
      ``LIFTED <X> <Y> <Z>``

    The freeze (`SetSimulatePhysics(False)`) is reversed by
    :func:`probe_restore_code` after the screenshot.
    """
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        f"target = '{label}'\n"
        "found = None\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    if a.GetActorLabel() == target:\n"
        "        found = a; break\n"
        "if not found:\n"
        "    print(f'NOT_FOUND {target}')\n"
        "else:\n"
        "    loc = found.K2_GetActorLocation()\n"
        "    print(f'PRE_POS {loc.X:.2f} {loc.Y:.2f} {loc.Z:.2f}')\n"
        "    mc = found.StaticMeshComponent\n"
        "    try:\n"
        "        mc.SetSimulatePhysics(False)\n"
        "    except Exception:\n"
        "        pass\n"
        "    v = ur.CoreUObject.Vector(); v.X=loc.X; v.Y=loc.Y; "
        f"v.Z=loc.Z + ({dz})\n"
        "    found.K2_SetActorLocation(v, False)\n"
        "    out = found.K2_GetActorLocation()\n"
        "    print(f'LIFTED {out.X:.2f} {out.Y:.2f} {out.Z:.2f}')\n"
    )


def probe_restore_code(label: str, x: float, y: float, z: float,
                        re_enable_physics: bool = True) -> str:
    """Snap the probed actor back to (x, y, z) and re-enable physics.
    ``re_enable_physics`` should be True for the cube; harmless for
    non-physics actors (they're already kinematic)."""
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        f"target = '{label}'\n"
        "found = None\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    if a.GetActorLabel() == target:\n"
        "        found = a; break\n"
        "if not found:\n"
        "    print(f'NOT_FOUND {target}')\n"
        "else:\n"
        "    v = ur.CoreUObject.Vector(); "
        f"v.X={x}; v.Y={y}; v.Z={z}\n"
        "    found.K2_SetActorLocation(v, False)\n"
        "    mc = found.StaticMeshComponent\n"
        f"    if {bool(re_enable_physics)}:\n"
        "        try:\n"
        "            mc.SetSimulatePhysics(True)\n"
        "            zero = ur.CoreUObject.Vector(); zero.X=0; zero.Y=0; zero.Z=0\n"
        "            mc.SetPhysicsLinearVelocity(zero, False, '')\n"
        "            mc.SetPhysicsAngularVelocityInDegrees(zero, False, '')\n"
        "        except Exception:\n"
        "            pass\n"
        f"    print(f'RESTORED {x} {y} {z}')\n"
    )


_PRE_POS_RE = re.compile(r"PRE_POS\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


def parse_pre_pos(stdout: str) -> Optional[tuple[float, float, float]]:
    for line in stdout.splitlines():
        m = _PRE_POS_RE.search(line)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None


# ---------------------------------------------------------------------------
# Settle physics + read final cube pose. Used during verify.
# ---------------------------------------------------------------------------
def cube_pose_code(label: str) -> str:
    return (
        "import unreal_runtime as ur\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        f"target = '{label}'\n"
        "found = None\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    if a.GetActorLabel() == target:\n"
        "        found = a; break\n"
        "if not found:\n"
        "    print(f'POSE_MISSING {target}')\n"
        "else:\n"
        "    loc = found.K2_GetActorLocation()\n"
        "    mc = found.StaticMeshComponent\n"
        "    try:\n"
        "        v = mc.GetPhysicsLinearVelocity()\n"
        "        speed = (v.X**2 + v.Y**2 + v.Z**2) ** 0.5\n"
        "    except Exception:\n"
        "        speed = 0.0\n"
        f"    print(f'POSE {{loc.X:.2f}} {{loc.Y:.2f}} {{loc.Z:.2f}} {{speed:.2f}}')\n"
    )


_POSE_RE = re.compile(r"POSE\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")


def parse_pose(stdout: str) -> Optional[tuple[float, float, float, float]]:
    for line in stdout.splitlines():
        m = _POSE_RE.search(line)
        if m:
            x, y, z, sp = m.groups()
            return float(x), float(y), float(z), float(sp)
    return None


def verify_pose_against_target(pose: tuple, params: dict) -> dict:
    """Apply the same xyz tolerance used in the original lean harness."""
    x, y, z, _ = pose
    v = params["verification"]
    eps = v["epsilon"]
    z_ok = abs(z - v["expected_z"]) < eps
    x_ok = v["valid_x_range"][0] <= x <= v["valid_x_range"][1]
    y_ok = v["valid_y_range"][0] <= y <= v["valid_y_range"][1]
    passed = z_ok and x_ok and y_ok
    if passed:
        reason = "ok"
    else:
        bits = []
        if not z_ok:
            bits.append(f"z {z:.1f} != {v['expected_z']:.1f}±{eps}")
        if not x_ok:
            bits.append(f"x {x:.1f} ∉ {v['valid_x_range']}")
        if not y_ok:
            bits.append(f"y {y:.1f} ∉ {v['valid_y_range']}")
        reason = " | ".join(bits)
    return {
        "passed": passed,
        "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
        "expected_z": v["expected_z"],
        "valid_x_range": v["valid_x_range"],
        "valid_y_range": v["valid_y_range"],
        "epsilon": eps,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Stdout / parser helpers
# ---------------------------------------------------------------------------
def unwrap(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    inner = resp.get("result", resp)
    if isinstance(inner, dict):
        return str(inner.get("result", ""))
    return str(inner)


# ---------------------------------------------------------------------------
# Action JSON parsing — agent emits {"cmd": "...", ...} inside a ```json
# fence (or a bare json object).
# ---------------------------------------------------------------------------
_JSON_FENCE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def extract_action(text: str) -> Optional[dict]:
    for m in _JSON_FENCE.finditer(text):
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict) and "cmd" in d:
                return d
        except Exception:
            continue
    for m in re.finditer(r'\{[^{}]*"cmd"[\s\S]*?\}', text):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and "cmd" in d:
                return d
        except Exception:
            continue
    return None


def extract_thought(text: str) -> str:
    return text.split("```", 1)[0].strip()


# ---------------------------------------------------------------------------
# Screenshot helper. Same pattern as scene_ground.
# ---------------------------------------------------------------------------
async def take_screenshot(engine, name: str, workspace: Path,
                          screenshot_dir: Optional[Path] = None) -> Path:
    from veriworld.common.screenshot import capture_runtime_screenshot
    return await capture_runtime_screenshot(engine, name, workspace,
                                            screenshot_dir=screenshot_dir)


# ---------------------------------------------------------------------------
# Top-level setup driver. Switches level, cleans, spawns scene.
# ---------------------------------------------------------------------------
async def open_level_and_setup(engine, params: dict) -> None:
    out = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_SWITCHING" in out:
        await asyncio.sleep(5.0)
    # Confirm we're on Untitled (re-fetch since OpenLevel invalidates handle)
    confirm = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_READY" not in confirm:
        log.warning("level didn't settle to Untitled: %r", confirm.strip())
    await asyncio.sleep(1.0)
    await engine.python_exec(CLEAN_SCENE_CODE)
    await asyncio.sleep(0.5)
    out = unwrap(await engine.python_exec(setup_scene_code(params)))
    log.info("setup_scene: %s", out.strip().split('\n')[-1])
    await asyncio.sleep(1.0)


# ---------------------------------------------------------------------------
# Settle + verify driver. Returns verdict dict.
# ---------------------------------------------------------------------------
async def settle_and_verify(engine, params: dict) -> dict:
    cube_label = "PLACE_OBJ_" + params["cube"]["obj_label"].split("_")[-1]
    v = params["verification"]
    deadline = asyncio.get_event_loop().time() + v["max_settle_s"]
    settled_for = 0.0
    last_pose = None
    sample_dt = 0.4
    while asyncio.get_event_loop().time() < deadline:
        out = unwrap(await engine.python_exec(cube_pose_code(cube_label)))
        pose = parse_pose(out)
        if pose is None:
            await asyncio.sleep(sample_dt)
            continue
        last_pose = pose
        if pose[3] < v["settle_speed"]:
            settled_for += sample_dt
            if settled_for >= v["settle_min_s"]:
                break
        else:
            settled_for = 0.0
        await asyncio.sleep(sample_dt)
    if last_pose is None:
        raise RuntimeError("Cube state unavailable during verification; episode is unscored")
    return verify_pose_against_target(last_pose, params)


__all__ = [
    "HERE",
    "LEVEL_SWITCH_CODE", "CLEAN_SCENE_CODE",
    "setup_scene_code", "open_level_and_setup",
    "QUERY_STATE_CODE", "parse_state", "annotate_with_colors",
    "move_to_code", "lift_code",
    "cube_pose_code", "parse_pose",
    "settle_and_verify", "verify_pose_against_target",
    "unwrap", "extract_action", "extract_thought",
    "take_screenshot",
]
