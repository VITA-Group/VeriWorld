"""Per-seed scene parameters for box_fold (cube-only for now).

Same seed → same cube size + spawn jitter + face-color palette
shuffle + UNFOLDING (which spanning tree of the cube's face-
adjacency graph the flat net uses). The agent's task is identical
regardless of seed; the seed varies the visual surface AND the
specific unfolding so we can measure how robust models are to
different starting configurations.

Convention:
  Edges of a face in its local frame —
    'bottom' -> -Y edge   (rotation axis +X)
    'top'    -> +Y edge   (rotation axis +X)
    'left'   -> -X edge   (rotation axis +Y)
    'right'  -> +X edge   (rotation axis +Y)
  In the FLAT net, every face has identity rotation (local axes =
  world axes), so each child is laid relative to its parent by the
  edge offset below.
"""

from __future__ import annotations

import math
import random
from typing import Any


# Distinct, vivid hues so adjacent faces are easy to tell apart in
# screenshots. Sampled WITHOUT replacement so all 6 faces have
# different colors per seed.
_FACE_COLORS = [
    (0.95, 0.20, 0.20),   # red
    (0.95, 0.55, 0.10),   # orange
    (0.95, 0.85, 0.10),   # yellow
    (0.20, 0.75, 0.20),   # green
    (0.10, 0.45, 0.85),   # blue
    (0.55, 0.20, 0.80),   # purple
    (0.95, 0.15, 0.95),   # magenta
    (0.10, 0.85, 0.85),   # cyan
    (1.00, 0.50, 0.00),   # bright orange
    (0.55, 0.10, 0.95),   # vivid purple
    (1.00, 0.10, 0.55),   # hot pink
    (0.55, 0.95, 0.10),   # chartreuse
]


# Each unfolding is a spanning tree of the cube's face adjacency
# graph. ``tree`` lists faces in BFS order with (label, parent_label,
# edge_of_parent_attached_to). The first entry is the root and has
# parent = None. Cube face adjacency: each face is adjacent to all
# others EXCEPT its opposite (bottom-top, front-back, left-right are
# the 3 non-adjacent pairs).
# Spanning trees expressed as (face_idx, parent_idx, edge_of_parent).
# face_idx 0 is always the root and gets the "bottom" label after
# label simulation. Other labels are computed at build time from
# folding the net to a closed cube. Trees with 2x2 blocks (which
# can't fold to a cube) are rejected automatically by _assign_labels.
_UNFOLDINGS: list[dict[str, Any]] = [
    # 1-4-1 family — row of 4 sides + 1 above + 1 below
    {"name": "row4_endend", "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "right"),    (3, 2, "right"),
        (4, 3, "right"),   (5, 1, "top"),
    ]},
    {"name": "row4_off1",  "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "right"),    (3, 2, "right"),
        (4, 3, "right"),   (5, 2, "top"),
    ]},
    {"name": "row4_off2",  "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "right"),    (3, 2, "right"),
        (4, 3, "right"),   (5, 3, "top"),
    ]},
    {"name": "row4_oppo",  "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "right"),    (3, 2, "right"),
        (4, 3, "right"),   (5, 4, "top"),
    ]},
    {"name": "row4_center", "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "left"),     (3, 1, "right"),
        (4, 3, "right"),   (5, 1, "top"),
    ]},
    {"name": "row4_centeradj", "tree": [
        (0, None, None),
        (1, 0, "top"),     (2, 1, "left"),     (3, 1, "right"),
        (4, 3, "right"),   (5, 3, "top"),
    ]},
    # T / 3-1-1-1 family
    {"name": "t_long_stem", "tree": [
        (0, None, None),
        (1, 0, "bottom"),  (2, 1, "bottom"),   (3, 2, "bottom"),
        (4, 0, "left"),    (5, 0, "right"),
    ]},
    # Z / staircase
    {"name": "z_step", "tree": [
        (0, None, None),
        (1, 0, "bottom"),  (2, 1, "left"),     (3, 0, "top"),
        (4, 3, "right"),   (5, 4, "top"),
    ]},
    # 2-2-2 staircase
    {"name": "stair_222", "tree": [
        (0, None, None),
        (1, 0, "right"),   (2, 1, "top"),      (3, 2, "right"),
        (4, 3, "top"),     (5, 4, "right"),
    ]},
]


# In the flat net, every parent has identity rotation, so each
# edge corresponds to a fixed (Δx, Δy) offset (in units of S) for
# placing the child face center.
_EDGE_OFFSET = {
    "bottom": (0.0, -1.0),
    "top":    (0.0, +1.0),
    "left":   (-1.0, 0.0),
    "right":  (+1.0, 0.0),
}

# Hinge offset (in parent's LOCAL frame, units of S) for fold simulation.
_HINGE_LOCAL_OFFSET = {
    "bottom": (0.0, -0.5, 0.0),
    "top":    (0.0, +0.5, 0.0),
    "left":   (-0.5, 0.0, 0.0),
    "right":  (+0.5, 0.0, 0.0),
}
# Hinge axis (in parent's local frame).
_HINGE_LOCAL_AXIS = {
    "bottom": (1.0, 0.0, 0.0),
    "top":    (1.0, 0.0, 0.0),
    "left":   (0.0, 1.0, 0.0),
    "right":  (0.0, 1.0, 0.0),
}
# Right-hand-rule angle (degrees) that lifts child face UP at flat time.
_EDGE_LIFT_ANGLE = {
    "bottom": -90.0,
    "top":    +90.0,
    "left":   +90.0,
    "right":  -90.0,
}


def _axis_angle_to_matrix(axis: tuple, angle_rad: float) -> tuple:
    """Right-hand-rule rotation matrix for axis-angle."""
    c = math.cos(angle_rad); s = math.sin(angle_rad); k = 1.0 - c
    ax, ay, az = axis
    return (
        (c + ax*ax*k,    ax*ay*k - az*s, ax*az*k + ay*s),
        (ay*ax*k + az*s, c + ay*ay*k,    ay*az*k - ax*s),
        (az*ax*k - ay*s, az*ay*k + ax*s, c + az*az*k),
    )


def _matmul(A: tuple, B: tuple) -> tuple:
    return tuple(
        tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _matvec(M: tuple, v: tuple) -> tuple:
    return tuple(sum(M[i][k] * v[k] for k in range(3)) for i in range(3))


_IDENTITY = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _simulate_close_unitcube(tree: list[tuple]) -> dict[int, tuple]:
    """Simulate folding the spanning tree using canonical lift angles
    on a unit cube (S=1, no thickness). Returns {face_idx: (x, y, z)}
    of each face center after closing.

    The root is at (0,0,0) and stays at world origin. The cube extends
    in +Z above the root, so cube center ends at (0,0,0.5) and face
    centers at half-edge offsets from there.
    """
    # Initial flat positions and rotations (identity).
    pos: dict[int, tuple] = {}
    rot: dict[int, tuple] = {}
    children: dict[int, list[int]] = {}
    edge_of: dict[int, str] = {}

    root = None
    for face_idx, parent_idx, edge in tree:
        children.setdefault(face_idx, [])
        if parent_idx is None:
            root = face_idx
            pos[face_idx] = (0.0, 0.0, 0.0)
            rot[face_idx] = _IDENTITY
        else:
            ox, oy = _EDGE_OFFSET[edge]
            px, py, pz = pos[parent_idx]
            pos[face_idx] = (px + ox, py + oy, pz)
            rot[face_idx] = _IDENTITY
            children.setdefault(parent_idx, []).append(face_idx)
            edge_of[face_idx] = edge

    # Apply folds in BFS order (parent before children).
    queue = [root]
    visited = {root}
    while queue:
        cur = queue.pop(0)
        for child in children.get(cur, []):
            if child in visited:
                continue
            visited.add(child)
            edge = edge_of[child]
            # Hinge in parent's CURRENT world frame.
            local_off = _HINGE_LOCAL_OFFSET[edge]
            local_axis = _HINGE_LOCAL_AXIS[edge]
            parent_rot = rot[cur]
            parent_pos = pos[cur]
            hinge_w = tuple(parent_pos[i] + _matvec(parent_rot, local_off)[i] for i in range(3))
            axis_w = _matvec(parent_rot, local_axis)
            angle_rad = math.radians(_EDGE_LIFT_ANGLE[edge])
            delta_R = _axis_angle_to_matrix(axis_w, angle_rad)
            # Collect subtree of child (including child).
            sub = []
            sq = [child]
            while sq:
                f = sq.pop(0)
                sub.append(f)
                sq.extend(children.get(f, []))
            for f in sub:
                old_p = pos[f]
                rel = tuple(old_p[i] - hinge_w[i] for i in range(3))
                new_rel = _matvec(delta_R, rel)
                pos[f] = tuple(hinge_w[i] + new_rel[i] for i in range(3))
                rot[f] = _matmul(delta_R, rot[f])
            queue.append(child)
    return pos


# Canonical closed-cube face centers (S=1, root bottom at origin,
# cube extends +Z so cube center is at (0, 0, 0.5)).
_CUBE_FACE_CENTERS = {
    "bottom": (0.0, 0.0, 0.0),
    "top":    (0.0, 0.0, 1.0),
    "front":  (0.0, -0.5, 0.5),
    "back":   (0.0, +0.5, 0.5),
    "left":   (-0.5, 0.0, 0.5),
    "right":  (+0.5, 0.0, 0.5),
}


def _assign_labels(tree: list[tuple]) -> dict[int, str]:
    """Run fold simulation, then assign each face the cube-face label
    of the closed-cube center it ends up nearest to. Raises ValueError
    if the assignment is not a bijection (which means the hexomino
    can't fold — usually a 2x2 block in the net).
    """
    closed = _simulate_close_unitcube(tree)
    idx_to_label: dict[int, str] = {}
    used: set[str] = set()
    for face_idx in sorted(closed.keys()):
        fp = closed[face_idx]
        best_label = None
        best_d2 = float("inf")
        for label, cp in _CUBE_FACE_CENTERS.items():
            if label in used:
                continue
            d2 = sum((fp[k] - cp[k]) ** 2 for k in range(3))
            if d2 < best_d2:
                best_d2 = d2
                best_label = label
        if best_d2 > 0.05:  # > ~22% of S off — bad fold
            raise ValueError(
                f"face {face_idx} closed at {fp} doesn't match any cube "
                f"face center (closest={best_label} at d²={best_d2:.3f})"
            )
        idx_to_label[face_idx] = best_label
        used.add(best_label)
    return idx_to_label


def _build_faces_and_hinges(unfolding: dict) -> tuple[list[dict], list[dict]]:
    """From a tree spec (indexed), derive per-face flat positions and
    hinge list, AND assign cube-face labels by folding-simulation.

    Returns (faces, hinges) where:
      faces[i]  = {index, label, parent, edge, flat_dx, flat_dy}
      hinges[i] = {id, parent, child, edge}
    """
    tree = unfolding["tree"]

    # Auto-assign labels by simulating fold to closed cube.
    # Raises if the hexomino has a 2x2 block or otherwise can't close.
    idx_to_label = _assign_labels(tree)

    # Compute flat positions in units of S (relative to root).
    positions: dict[int, tuple[float, float]] = {}
    for face_idx, parent_idx, edge in tree:
        if parent_idx is None:
            positions[face_idx] = (0.0, 0.0)
        else:
            px, py = positions[parent_idx]
            ox, oy = _EDGE_OFFSET[edge]
            positions[face_idx] = (px + ox, py + oy)

    faces: list[dict] = []
    hinges: list[dict] = []
    hinge_id = 0
    for face_idx, parent_idx, edge in tree:
        dx, dy = positions[face_idx]
        faces.append({
            "index": face_idx,
            "label": idx_to_label[face_idx],
            "parent": parent_idx,
            "edge": edge,
            "flat_dx": dx,
            "flat_dy": dy,
        })
        if parent_idx is not None:
            hinges.append({
                "id": hinge_id,
                "parent": parent_idx,
                "child": face_idx,
                "edge": edge,
            })
            hinge_id += 1
    faces.sort(key=lambda f: f["index"])
    return faces, hinges


def generate(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    unfold_idx = rng.randrange(len(_UNFOLDINGS))
    unfolding = _UNFOLDINGS[unfold_idx]
    faces, hinges = _build_faces_and_hinges(unfolding)

    S = round(rng.uniform(110.0, 180.0), 1)
    spawn_x = round(rng.uniform(-80.0, 80.0), 1)
    spawn_y = round(rng.uniform(-80.0, 80.0), 1)

    # 6 distinct colors, one per face (indexed by face index).
    colors = rng.sample(_FACE_COLORS, len(faces))

    return {
        "test_id": "box_fold",
        "seed": seed,
        "polyhedron": "cube",
        "unfolding_id": unfold_idx,
        "unfolding_name": unfolding["name"],
        "S": S,
        "T": 5.0,
        "table_z": 350.0,
        "spawn_xy": [spawn_x, spawn_y],
        "colors": [list(c) for c in colors],
        "faces": faces,
        "hinges": hinges,
        "verification": {
            "tolerance_cm": 8.0,
            "max_rounds": 15,
        },
    }


def list_unfoldings() -> list[str]:
    return [u["name"] for u in _UNFOLDINGS]


if __name__ == "__main__":
    import json, sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(json.dumps(generate(seed), indent=2))
