"""Shared building blocks for box_fold harnesses.

* Switch UE to ``Untitled`` level
* Spawn the flat T-cross net (6 thin Cube slabs lying on the table)
* Persist face actor list + hinge tree in ``builtins`` so subsequent
  python_exec calls can apply ``fold(hinge_id, angle)`` without
  re-spawning
* Read back face poses and compare to canonical closed-cube targets
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


# ---------------------------------------------------------------------------
# Level switch (Untitled, like billiards / mazenavfps / place_on_platform)
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
# Spawn the flat net + register fold tree in builtins.
# ---------------------------------------------------------------------------
def setup_scene_code(params: dict) -> str:
    p_json = json.dumps(params)
    return (
        "import unreal_runtime as ur\n"
        "import json, builtins, math\n"
        f"P = json.loads(r'''{p_json}''')\n"
        "S = P['S']; T = P['T']\n"
        "TABLE_Z = P['table_z']\n"
        "SPAWN_X, SPAWN_Y = P['spawn_xy']\n"
        "COLORS = P['colors']\n"
        "FACES = P['faces']\n"
        "world = ur.Engine.GetDefaultWorld()\n"
        "for a in world.GetActorsOfClass(ur.Engine.StaticMeshActor):\n"
        "    lbl = a.GetActorLabel()\n"
        '    if lbl.startswith("BFOLD_") or lbl == "Cube":\n'
        "        a.K2_DestroyActor()\n"
        "BASE_MAT = ur.StaticLoadObject(ur.Engine.MaterialInterface, None,\n"
        '    "/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial")\n'
        "CUBE_MESH = ur.RuntimeCore.RuntimeglTFLibrary.LoadStaticMeshFromPath(\n"
        '    "/Engine/BasicShapes/Cube.Cube")\n'
        "flat_z = TABLE_Z + T / 2.0\n"
        # Flat net positions come from params['faces'] — each face has
        # flat_dx / flat_dy in units of S relative to the root center.
        "n_faces = len(FACES)\n"
        "face_actors = [None] * n_faces\n"
        "for f in FACES:\n"
        "    i = f['index']\n"
        "    fx = SPAWN_X + f['flat_dx'] * S\n"
        "    fy = SPAWN_Y + f['flat_dy'] * S\n"
        "    transform = ur.CoreUObject.Transform()\n"
        "    transform.Translation = ur.CoreUObject.Vector(fx, fy, flat_z)\n"
        "    transform.Rotation = ur.Engine.KismetMathLibrary.Conv_RotatorToQuaternion(\n"
        "        ur.CoreUObject.Rotator(0, 0, 0))\n"
        "    sv = ur.CoreUObject.Vector(); sv.X = S/100.0; sv.Y = S/100.0; sv.Z = T/100.0\n"
        "    transform.Scale3D = sv\n"
        "    actor = world.SpawnActorEx(ur.Engine.StaticMeshActor, transform, 1)\n"
        "    mc = actor.StaticMeshComponent\n"
        "    mc.SetMobility(2)\n"
        "    mc.SetStaticMesh(CUBE_MESH)\n"
        "    mc.SetSimulatePhysics(False)\n"
        "    mc.SetCollisionEnabled(0)\n"
        "    actor.SetActorLabel(f\"BFOLD_{i}_{f['label']}\")\n"
        # Apply the colored MID to ALL material slots so both sides
        # of the thin slab (and any extra slots a future mesh might
        # add) share one color.
        "    if BASE_MAT:\n"
        "        r, g, b = COLORS[i]\n"
        "        col = ur.CoreUObject.LinearColor(); col.R, col.G, col.B, col.A = r, g, b, 1.0\n"
        "        n_slots = max(1, mc.GetNumMaterials())\n"
        "        for slot in range(n_slots):\n"
        "            mc.SetMaterial(slot, BASE_MAT)\n"
        "            mid = mc.CreateDynamicMaterialInstance(slot)\n"
        "            if mid:\n"
        '                mid.SetVectorParameterValue("Color", col)\n'
        "    face_actors[i] = actor\n"
        "builtins._BFOLD_FACES = face_actors\n"
        "builtins._BFOLD_HINGES = P['hinges']\n"
        "children = {i: [] for i in range(n_faces)}\n"
        "for h in P['hinges']:\n"
        "    children[h['parent']].append(h['child'])\n"
        "builtins._BFOLD_CHILDREN = children\n"
        "builtins._BFOLD_PARAMS = P\n"
        # Auto-frame camera using the bbox of all face centers (works
        # for any unfolding shape, not just T-cross).
        "all_dx = [f['flat_dx'] for f in FACES]\n"
        "all_dy = [f['flat_dy'] for f in FACES]\n"
        "min_x = SPAWN_X + (min(all_dx) - 0.5) * S\n"
        "max_x = SPAWN_X + (max(all_dx) + 0.5) * S\n"
        "min_y = SPAWN_Y + (min(all_dy) - 0.5) * S\n"
        "max_y = SPAWN_Y + (max(all_dy) + 0.5) * S\n"
        "cx = (min_x + max_x) / 2; cy = (min_y + max_y) / 2\n"
        "spread = max(max_x - min_x, max_y - min_y, 400.0) + 200.0\n"
        "cam_dist = spread / (2 * math.tan(math.radians(32.0)))\n"
        "gm = ur.Engine.GetGameMode()\n"
        "pawns = world.GetActorsOfClass(gm.DefaultPawnClass)\n"
        "if pawns:\n"
        "    p = pawns[0]\n"
        "    cx2 = cx + cam_dist * 0.6; cy2 = cy - cam_dist * 0.5; cz2 = TABLE_Z + cam_dist * 0.55\n"
        "    p.K2_SetActorLocation(ur.CoreUObject.Vector(cx2, cy2, cz2), False)\n"
        "    yaw = math.degrees(math.atan2(cy - cy2, cx - cx2))\n"
        "    rot = ur.CoreUObject.Rotator(); rot.Pitch = -35.0; rot.Yaw = yaw; rot.Roll = 0.0\n"
        "    p.GetController().SetControlRotation(rot)\n"
        f"print(f'BFOLD_READY n_faces={{n_faces}} S={{S}} unfolding={{P[\"unfolding_name\"]}}')\n"
    )


# ---------------------------------------------------------------------------
# Apply one fold via Rodrigues, walking the BFS subtree.
# ---------------------------------------------------------------------------
def fold_code(hinge_id: int, angle_deg: float) -> str:
    return (
        "import unreal_runtime as ur\n"
        "import builtins, math\n"
        "hinges = builtins._BFOLD_HINGES\n"
        "faces = builtins._BFOLD_FACES\n"
        "children = builtins._BFOLD_CHILDREN\n"
        f"hinge_id = {hinge_id}\n"
        f"angle_deg = {angle_deg}\n"
        "if hinge_id < 0 or hinge_id >= len(hinges):\n"
        "    print(f'BFOLD_BAD_HINGE {hinge_id}')\n"
        "else:\n"
        "    h = hinges[hinge_id]\n"
        "    parent_actor = faces[h['parent']]\n"
        "    edge = h['edge']\n"
        "    if edge == 'top': hl = ur.CoreUObject.Vector(0, 50, 0); al = ur.CoreUObject.Vector(1, 0, 0)\n"
        "    elif edge == 'bottom': hl = ur.CoreUObject.Vector(0, -50, 0); al = ur.CoreUObject.Vector(1, 0, 0)\n"
        "    elif edge == 'left': hl = ur.CoreUObject.Vector(-50, 0, 0); al = ur.CoreUObject.Vector(0, 1, 0)\n"
        "    elif edge == 'right': hl = ur.CoreUObject.Vector(50, 0, 0); al = ur.CoreUObject.Vector(0, 1, 0)\n"
        "    parent_xform = parent_actor.GetTransform()\n"
        "    hinge_w = ur.Engine.KismetMathLibrary.TransformLocation(parent_xform, hl)\n"
        "    axis_w = ur.Engine.KismetMathLibrary.TransformDirection(parent_xform, al)\n"
        "    rad = math.radians(angle_deg)\n"
        "    cos_a = math.cos(rad); sin_a = math.sin(rad)\n"
        "    ax, ay, az = axis_w.X, axis_w.Y, axis_w.Z\n"
        "    delta_rot = ur.Engine.KismetMathLibrary.RotatorFromAxisAndAngle(axis_w, angle_deg)\n"
        "    queue = [h['child']]; moved = []\n"
        "    while queue:\n"
        "        cur = queue.pop(0); moved.append(cur)\n"
        "        queue.extend(children[cur])\n"
        "    for idx in moved:\n"
        "        actor = faces[idx]\n"
        "        p = actor.K2_GetActorLocation(); r = actor.K2_GetActorRotation()\n"
        "        dx = p.X - hinge_w.X; dy = p.Y - hinge_w.Y; dz = p.Z - hinge_w.Z\n"
        "        dot = dx * ax + dy * ay + dz * az\n"
        "        nx = dx*cos_a + (ay*dz - az*dy)*sin_a + ax*dot*(1-cos_a)\n"
        "        ny = dy*cos_a + (az*dx - ax*dz)*sin_a + ay*dot*(1-cos_a)\n"
        "        nz = dz*cos_a + (ax*dy - ay*dx)*sin_a + az*dot*(1-cos_a)\n"
        "        new_pos = ur.CoreUObject.Vector(hinge_w.X + nx, hinge_w.Y + ny, hinge_w.Z + nz)\n"
        "        new_rot = ur.Engine.KismetMathLibrary.ComposeRotators(r, delta_rot)\n"
        "        actor.K2_SetActorLocation(new_pos, False)\n"
        "        actor.K2_SetActorRotation(new_rot, False)\n"
        "    print(f'FOLDED hinge_id={hinge_id} angle={angle_deg} subtree={moved}')\n"
    )


# ---------------------------------------------------------------------------
# Read back every face's center → JSON line ``BFOLD_STATES:[...]``.
# ---------------------------------------------------------------------------
QUERY_STATE_CODE = (
    "import unreal_runtime as ur\n"
    "import builtins, json\n"
    "faces = builtins._BFOLD_FACES\n"
    "out = []\n"
    "for i, a in enumerate(faces):\n"
    "    p = a.K2_GetActorLocation()\n"
    "    lbl = a.GetActorLabel().split('_', 2)[-1] if '_' in a.GetActorLabel() else a.GetActorLabel()\n"
    "    out.append({'index': i, 'label': lbl,\n"
    "                'position': [round(p.X, 1), round(p.Y, 1), round(p.Z, 1)]})\n"
    "print('BFOLD_STATES:' + json.dumps(out))\n"
)


_EDGE_HUMAN = {
    "bottom": "parent's -Y edge",
    "top":    "parent's +Y edge",
    "left":   "parent's -X edge",
    "right":  "parent's +X edge",
}


def format_hinge_table(params: dict) -> str:
    """Render this seed's hinge tree as a stable plain-text block
    for inclusion in agent prompts.
    """
    label_by_idx = {f["index"]: f["label"] for f in params["faces"]}
    lines = []
    for h in params["hinges"]:
        p = label_by_idx[h["parent"]]
        c = label_by_idx[h["child"]]
        lines.append(
            f"  hinge {h['id']} : {p} ({h['parent']}) -> {c} ({h['child']}) , "
            f"edge='{h['edge']}' ({_EDGE_HUMAN[h['edge']]})"
        )
    return "\n".join(lines)


def format_flat_layout(params: dict) -> str:
    """Render the flat net layout (face centers in (Δx, Δy) units of S
    relative to the root) as plain text for prompts."""
    lines = []
    for f in sorted(params["faces"], key=lambda x: x["index"]):
        lines.append(
            f"  face {f['index']:>1d} ({f['label']:>6s}): "
            f"flat offset ({f['flat_dx']:+.0f}, {f['flat_dy']:+.0f}) * S"
        )
    return "\n".join(lines)


def validate_states(states):
    labels = {'bottom', 'top', 'front', 'back', 'left', 'right'}
    if not isinstance(states, list) or len(states) != 6:
        raise RuntimeError('BoxFold requires six observed faces')
    try:
        valid = ({s['label'] for s in states} == labels and
                 {s['index'] for s in states} == set(range(6)))
        for state in states:
            position = state['position']
            valid = valid and isinstance(position, list) and len(position) == 3
            valid = valid and all(type(v) in (int, float) and math.isfinite(v) for v in position)
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise RuntimeError('Incomplete or invalid BoxFold face state')
    return states


def parse_states(stdout: str) -> list[dict]:
    for line in stdout.splitlines():
        if line.startswith('BFOLD_STATES:'):
            try:
                return validate_states(json.loads(line[len('BFOLD_STATES:'):]))
            except (ValueError, TypeError) as error:
                raise RuntimeError('Malformed BoxFold state response') from error
    raise RuntimeError('Missing BoxFold state response')


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------
def expected_closed_centers(params: dict) -> dict:
    """Canonical closed-cube face centers (z = TABLE_Z + S/2 is the
    cube center's height; faces are at half-edge offsets from that)."""
    S = params["S"]
    sx, sy = params["spawn_xy"]
    cx, cy, cz = sx, sy, params["table_z"] + S / 2.0
    return {
        "bottom": (cx,        cy,        cz - S / 2.0),
        "top":    (cx,        cy,        cz + S / 2.0),
        "front":  (cx,        cy - S / 2, cz),
        "back":   (cx,        cy + S / 2, cz),
        "left":   (cx - S / 2, cy,        cz),
        "right":  (cx + S / 2, cy,        cz),
    }


def verify_states(states: list[dict], params: dict) -> dict:
    validate_states(states)
    expected = expected_closed_centers(params)
    tol = params["verification"]["tolerance_cm"]
    per_face: list[dict] = []
    all_ok = True
    worst = 0.0
    for s in states:
        label = s["label"]
        if label not in expected:
            continue
        ex, ey, ez = expected[label]
        x, y, z = s["position"]
        d = math.sqrt((x - ex) ** 2 + (y - ey) ** 2 + (z - ez) ** 2)
        # Account for face thickness (slabs have T/2 offset along normal,
        # which produces a constant ~T/2 = 2.5 cm error even when the
        # cube is correctly closed). Subtract that before comparing.
        d_corr = max(0.0, d - params["T"] / 2.0)
        ok = d_corr <= tol
        if not ok:
            all_ok = False
        worst = max(worst, d_corr)
        per_face.append({"label": label, "got": [x, y, z], "want": [ex, ey, ez],
                          "err_cm": round(d_corr, 2), "ok": ok})
    return {
        "passed": all_ok,
        "worst_err_cm": round(worst, 2),
        "tolerance_cm": tol,
        "per_face": per_face,
        "reason": "ok" if all_ok else f"worst err {worst:.1f} cm > {tol} cm",
    }


# ---------------------------------------------------------------------------
# Stdout / parsing helpers
# ---------------------------------------------------------------------------
def unwrap(value) -> str:
    for _ in range(8):
        if isinstance(value, dict):
            if value.get('error') or value.get('success') is False:
                raise RuntimeError('BoxFold runtime execution failed')
            for key in ('result', 'output', 'stdout'):
                if key in value:
                    value = value[key]
                    break
            else:
                raise RuntimeError('Missing BoxFold runtime output')
        elif isinstance(value, str):
            if 'Traceback (most recent call last):' in value:
                raise RuntimeError('BoxFold runtime reported an exception')
            try:
                decoded = json.loads(value)
            except ValueError:
                return value
            if isinstance(decoded, dict) and any(k in decoded for k in ('result','output','stdout','error','success')):
                value = decoded
            else:
                return value
        else:
            raise RuntimeError('Invalid BoxFold runtime output')
    raise RuntimeError('BoxFold runtime envelope exceeds nesting limit')


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
# Screenshot (file-based, same as place_on_platform / scene_ground)
# ---------------------------------------------------------------------------
async def take_screenshot(engine, name: str, workspace: Path,
                          screenshot_dir: Optional[Path] = None) -> Path:
    from veriworld.common.screenshot import capture_runtime_screenshot
    override = screenshot_dir or os.environ.get("BFOLD_SCREENSHOT_DIR")
    return await capture_runtime_screenshot(engine, name, workspace,
        screenshot_dir=Path(override) if override else None)


async def open_level_and_setup(engine, params: dict) -> None:
    out = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_SWITCHING" in out:
        await asyncio.sleep(5.0)
    confirm = unwrap(await engine.python_exec(LEVEL_SWITCH_CODE))
    if "LEVEL_READY" not in confirm:
        raise RuntimeError("BoxFold level did not become ready")
    await asyncio.sleep(0.8)
    out = unwrap(await engine.python_exec(setup_scene_code(params)))
    if "BFOLD_READY" not in out:
        raise RuntimeError("BoxFold scene setup was not acknowledged")
    log.info("setup_scene: %s", out.strip().split('\n')[-1])
    await asyncio.sleep(0.6)


# ---------------------------------------------------------------------------
# Ground-truth solver — canonical fold sequence per polyhedron.
# Used as a pre-flight self-check that the unfolding is actually
# closable to within tolerance before we let the agent try.
# ---------------------------------------------------------------------------
# Right-hand rotation around the parent's local axis that lifts
# the child face UP (towards +Z) from the flat net. Holds for any
# spanning tree because every parent has identity rotation when its
# OWN parent has been folded — the local axes stay aligned with the
# fold convention regardless of depth in the tree.
_EDGE_LIFT_ANGLE = {
    "bottom": -90.0,   # child at parent's -Y edge, axis +X: -90 maps -Y -> +Z
    "top":    +90.0,   # child at parent's +Y edge, axis +X: +90 maps +Y -> +Z
    "left":   +90.0,   # child at parent's -X edge, axis +Y: +90 maps -X -> +Z
    "right":  -90.0,   # child at parent's +X edge, axis +Y: -90 maps +X -> +Z
}


def ground_truth_folds(params: dict) -> list[tuple[int, float]]:
    """Return canonical (hinge_id, angle_deg) sequence that closes
    the net for this seed.

    Works for ANY spanning-tree unfolding (not just T-cross): we
    do a BFS over hinges starting from the root face, and the angle
    for each fold depends only on which of the parent's local edges
    the child is attached to. Fold parents BEFORE their children so
    the child's hinge axis is already in the correct world frame
    when we apply its fold.
    """
    poly = params.get("polyhedron", "cube")
    if poly != "cube":
        raise ValueError(f"no ground-truth fold sequence for polyhedron={poly!r}")
    hinges = params["hinges"]
    by_parent: dict[int, list[dict]] = {}
    for h in hinges:
        by_parent.setdefault(h["parent"], []).append(h)
    root_idx = next(f["index"] for f in params["faces"] if f["parent"] is None)
    seq: list[tuple[int, float]] = []
    queue = [root_idx]
    visited = {root_idx}
    while queue:
        cur = queue.pop(0)
        for h in by_parent.get(cur, []):
            seq.append((h["id"], _EDGE_LIFT_ANGLE[h["edge"]]))
            if h["child"] not in visited:
                visited.add(h["child"])
                queue.append(h["child"])
    return seq


async def assert_seed_solvable(
    engine,
    params: dict,
    *,
    tol_cm: Optional[float] = None,
    settle_s: float = 0.15,
) -> dict:
    """Spawn fresh net, apply ground-truth folds, verify within
    tolerance. Raises RuntimeError if verification fails — that
    means the seed parameters or topology / hinge convention drifted
    out of sync. Caller should run this BEFORE the agent and then
    let the harness call ``open_level_and_setup`` again to respawn
    the flat net for the real episode.

    Returns the verdict dict (same shape as ``verify_states``) so
    callers can log it.
    """
    out = unwrap(await engine.python_exec(setup_scene_code(params)))
    if "BFOLD_READY" not in out:
        raise RuntimeError(f"GT setup failed: {out.strip()[:200]}")
    await asyncio.sleep(0.4)
    for hinge_id, angle in ground_truth_folds(params):
        await engine.python_exec(fold_code(hinge_id, angle))
        await asyncio.sleep(settle_s)
    state_out = unwrap(await engine.python_exec(QUERY_STATE_CODE))
    states = parse_states(state_out)
    p = dict(params)
    if tol_cm is not None:
        p = dict(params)
        p["verification"] = dict(params["verification"])
        p["verification"]["tolerance_cm"] = float(tol_cm)
    verdict = verify_states(states, p)
    if not verdict.get("passed"):
        raise RuntimeError(
            f"GT solver FAILED for seed={params.get('seed')} "
            f"(worst {verdict.get('worst_err_cm')} cm > "
            f"{verdict.get('tolerance_cm')} cm). per_face={verdict.get('per_face')}"
        )
    log.info(
        "GT check seed=%s PASS (worst %.2f cm <= %.1f cm)",
        params.get("seed"), verdict.get("worst_err_cm"), verdict.get("tolerance_cm"),
    )
    return verdict


__all__ = [
    "HERE",
    "LEVEL_SWITCH_CODE", "setup_scene_code", "fold_code",
    "QUERY_STATE_CODE", "parse_states",
    "expected_closed_centers", "verify_states",
    "open_level_and_setup",
    "ground_truth_folds", "assert_seed_solvable",
    "format_hinge_table", "format_flat_layout",
    "unwrap", "extract_action", "extract_thought", "take_screenshot",
]
