"""Seeded parameters for the billiards task.

Each seed fixes the initial rack layout (slight jitter) and the set of
coloured target balls. The table geometry and cue ball start are
**fixed within a difficulty preset** — what the agent sees varies only
in where the coloured balls sit initially and what the scene looks like.

Two difficulty presets:

* ``"standard"`` (default, original task): 800×800 square table, 5
  coloured balls in a 1+2+2 triangle, cue at the -X end. This is the
  setting the existing siblings were tuned + benchmarked on.
* ``"hard"``: 1500×800 long rectangular table, 10 coloured balls in
  a complete 1+2+3+4 triangle, cue at the -X end. Camera is raised
  to fit the longer side.

Standard branch is byte-identical to the pre-difficulty version (same
RNG sequence, same JSON keys, same values) so existing seed-0 params
hash matches.

Ported/adapted from
``VeriWorld-benchmark/.../navigation/debug/chaos/billiards/01_billiards_basic.py``.
"""

from __future__ import annotations

import json
import os
import random
from typing import Dict, List, Tuple


TABLE_CENTER_XY = [-500.0, -1500.0]     # offset from origin to dodge
                                        # residual voxel clutter on Untitled
TABLE_TOP_Z = 100.0
TABLE_THICKNESS = 20.0

RAIL_HEIGHT = 60.0
RAIL_THICKNESS = 30.0
HOLE_CORNER_GAP = 70.0

BALL_RADIUS = 22.0
# Differential damping: cue gets a bit of decay so it doesn't keep going
# after the break; coloured balls roll further so a hit actually carries
# them across the felt.
CUE_LINEAR_DAMPING = 0.15
CUE_ANGULAR_DAMPING = 0.15
BALL_LINEAR_DAMPING = 0.05
BALL_ANGULAR_DAMPING = 0.05

CUE_MAX_SPEED_CM_S = 1500.0

# Per-difficulty constants kept here so the standard preset's literals
# are byte-identical to the pre-difficulty version. Don't move into
# DIFFICULTY_PRESETS without first verifying the seed-0 hash.
TABLE_SIZE_X = 800.0           # standard preset (kept as module-level
TABLE_SIZE_Y = 800.0           # constant for backward import compat)

# Canonical rack colours.  Standard preset uses the first 5; hard
# preset uses all 10.  Order is stable so colour→id is reproducible.
RACK_COLORS: List[List[float]] = [
    [0.90, 0.12, 0.10],   # red
    [0.10, 0.22, 0.90],   # blue
    [0.95, 0.80, 0.10],   # yellow
    [0.20, 0.72, 0.22],   # green
    [0.90, 0.40, 0.82],   # pink
    [0.95, 0.55, 0.10],   # orange
    [0.10, 0.78, 0.85],   # cyan
    [0.55, 0.20, 0.78],   # purple
    [0.45, 0.30, 0.18],   # brown
    [0.05, 0.05, 0.05],   # black  (was gray — too close to white cue
                          #         in default lighting)
]
RACK_COLOR_NAMES: List[str] = [
    "red", "blue", "yellow", "green", "pink",
    "orange", "cyan", "purple", "brown", "black",
]


def _rack_positions_standard(rack_x: float, cy: float, s: float
                             ) -> List[Tuple[float, float]]:
    """Original 1+2+2 layout (5 balls). Frozen — do NOT alter."""
    return [
        (rack_x,                   cy),                          # apex
        (rack_x + s * 0.866,       cy + s * 0.500),              # row 2
        (rack_x + s * 0.866,       cy - s * 0.500),
        (rack_x + s * 1.732,       cy + s * 1.000),              # row 3 outer
        (rack_x + s * 1.732,       cy - s * 1.000),
    ]


def _rack_positions_triangle(rack_x: float, cy: float, s: float,
                             n_rows: int) -> List[Tuple[float, float]]:
    """Full 1+2+...+n_rows triangle (n_rows*(n_rows+1)/2 balls)."""
    out: List[Tuple[float, float]] = []
    for row in range(n_rows):
        row_x = rack_x + s * 0.866 * row
        for i in range(row + 1):
            offset = (i - row / 2.0) * s
            out.append((row_x, cy + offset))
    return out


def generate(seed: int = 0, difficulty: str = "standard") -> Dict:
    """Produce a params dict for one billiards episode.

    ``difficulty="standard"`` (default): 800×800 table, 5 balls, 1+2+2
    triangle. Reproduces the original task byte-for-byte.

    ``difficulty="hard"``: 1500×800 table, 10 balls, 1+2+3+4 triangle.
    Camera is raised via ``camera_height_cm`` in the returned dict so
    `_shared.py`'s setup code can frame the longer side.
    """
    if difficulty not in ("standard", "hard"):
        raise ValueError(f"unknown difficulty: {difficulty!r}")

    rng = random.Random(seed)
    cx, cy = TABLE_CENTER_XY

    if difficulty == "standard":
        table_size_x = TABLE_SIZE_X    # 800
        table_size_y = TABLE_SIZE_Y    # 800
        camera_height_cm = 900.0       # default camera (omittable)
        cue_lin_damp = CUE_LINEAR_DAMPING       # 0.15
        cue_ang_damp = CUE_ANGULAR_DAMPING      # 0.15
        ball_lin_damp = BALL_LINEAR_DAMPING     # 0.05
        ball_ang_damp = BALL_ANGULAR_DAMPING    # 0.05
        # Settle thresholds (omitted from params on standard so the
        # JSON stays byte-identical to the pre-difficulty version).
        soft_cap_s = None
        extended_cap_s = None
        hard_cap_freeze_vel = None
        cue_x = cx - table_size_x / 2.0 + 180.0
        rack_x = cx + table_size_x / 2.0 - 260.0
        s = BALL_RADIUS * 2.05
        jitter = 4.0
        positions = _rack_positions_standard(rack_x, cy, s)
        n_target = 5
    else:  # "hard"
        # Modest rectangle — only ~1.44× as long as wide. The first
        # try at 1500×800 was too long: cue→rack travel was tedious
        # and gave the agent no way to recover from a missed break.
        # 1150×800 keeps the "more balls + non-square table" hardness
        # angle without making distance the dominant difficulty.
        table_size_x = 1150.0
        table_size_y = 800.0
        # Camera height tuned so the long X axis fits in the default
        # ~90° vertical FOV with margin (visible width ≈ 2×height).
        camera_height_cm = 1000.0
        cue_lin_damp = CUE_LINEAR_DAMPING
        cue_ang_damp = CUE_ANGULAR_DAMPING
        ball_lin_damp = BALL_LINEAR_DAMPING
        ball_ang_damp = BALL_ANGULAR_DAMPING
        # 10 balls → longer collision chains. Bump the soft cap and
        # tighten the freeze-velocity threshold so rolling balls
        # aren't snapped to rest mid-trajectory.
        soft_cap_s = 15.0           # was 10
        extended_cap_s = 32.0       # was 25
        hard_cap_freeze_vel = 20.0  # was 50 — 20 cm/s is barely visible
        cue_x = cx - table_size_x / 2.0 + 180.0
        # Rack apex placed so the deepest row leaves ~120cm to the +X
        # rail. Apex sits at +X end roughly mirror to the cue at -X.
        rack_x = cx + table_size_x / 2.0 - 240.0
        # Wider rack spacing (2.20 vs 2.05) → 4.8 cm gap between balls
        # instead of 1.1, so jitter can no longer cause overlap.
        # Combined with reduced jitter (1 cm vs 4 cm) below this gives
        # a rack that stays put before the screenshot.
        s = BALL_RADIUS * 2.20
        jitter = 1.0
        positions = _rack_positions_triangle(rack_x, cy, s, n_rows=4)
        n_target = 10

    positions = [
        (p[0] + rng.uniform(-jitter, jitter),
         p[1] + rng.uniform(-jitter, jitter))
        for p in positions
    ]

    balls = [
        {"id": "cue",   "color_name": "white",
         "rgb": [0.96, 0.96, 0.92], "pos": [cue_x, cy]},
    ]
    for i, (pos, col, name) in enumerate(zip(positions,
                                              RACK_COLORS[:n_target],
                                              RACK_COLOR_NAMES[:n_target])):
        balls.append({
            "id": f"ball_{i}", "color_name": name, "rgb": list(col),
            "pos": [pos[0], pos[1]],
        })

    params = {
        "seed": seed,
        "table_center_xy": TABLE_CENTER_XY,
        "table_top_z": TABLE_TOP_Z,
        "table_size_x": table_size_x,
        "table_size_y": table_size_y,
        "table_thickness": TABLE_THICKNESS,
        "rail_height": RAIL_HEIGHT,
        "rail_thickness": RAIL_THICKNESS,
        "hole_corner_gap": HOLE_CORNER_GAP,
        "ball_radius": BALL_RADIUS,
        "cue_linear_damping": cue_lin_damp,
        "cue_angular_damping": cue_ang_damp,
        "ball_linear_damping": ball_lin_damp,
        "ball_angular_damping": ball_ang_damp,
        "cue_max_speed_cm_s": CUE_MAX_SPEED_CM_S,
        "balls": balls,
        "num_target_balls": n_target,
    }
    # Standard preset MUST serialise byte-identical to the
    # pre-difficulty version. Difficulty tag, camera height, and
    # settle-threshold overrides are only added for non-standard
    # presets so the standard hash stays stable.
    if difficulty != "standard":
        params["difficulty"] = difficulty
        params["camera_height_cm"] = camera_height_cm
        params["settle_soft_cap_s"] = soft_cap_s
        params["settle_extended_cap_s"] = extended_cap_s
        params["settle_hard_cap_freeze_vel_cm_s"] = hard_cap_freeze_vel

    print(f"[billiards.generate] seed={seed} difficulty={difficulty}, "
          f"{len(balls)} balls, table={table_size_x}x{table_size_y}")
    return params


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Generate one instance into an explicit output file")
    parser.add_argument("seed", type=int, nargs="?", default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--difficulty", choices=["standard", "hard"], default="standard")
    args = parser.parse_args()
    params = generate(args.seed, difficulty=args.difficulty)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(params, indent=2), encoding="utf-8")
