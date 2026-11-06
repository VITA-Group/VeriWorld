"""Per-seed scene parameters for place_on_platform.

Randomizes per seed:
  * cube side length, mass, color
  * platform dimensions (always larger than cube), color
  * 1-3 distractor boxes with random scale / color / position
  * ``obj_order`` shuffles the OBJ_i → role mapping so the cube
    isn't always at the same index

Same seed → same params across all harnesses + all models, for
clean apples-to-apples comparison.
"""

from __future__ import annotations

import random
from typing import Any


# ---------------------------------------------------------------------------
# Color palette. Cube gets the BLUE-ish entry; platform gets the YELLOW-ish
# entry. Distractors pick from the rest. Per-seed jitter keeps things varied.
# ---------------------------------------------------------------------------
def _jitter(rgb: tuple, rng: random.Random, amt: float = 0.08) -> list:
    return [round(min(1.0, max(0.0, c + rng.uniform(-amt, amt))), 3) for c in rgb]


# Distractor palette — high-saturation, well-spread hues that avoid
# cube-blue (≈ 0.1, 0.3, 1.0) and platform-yellow (≈ 1.0, 0.9, 0.1).
# Distractors sample WITHOUT replacement (up to 8 picks) so no two
# scene objects share a hue. Every entry has at least one channel near
# 0 and one near 1 so the colors are vivid in screenshots, not pastel.
_DISTRACTOR_COLORS = [
    (0.95, 0.10, 0.10),  # bright red
    (0.10, 0.85, 0.20),  # vivid green
    (0.95, 0.15, 0.95),  # magenta
    (0.10, 0.85, 0.85),  # cyan
    (1.00, 0.50, 0.00),  # pure orange
    (0.55, 0.10, 0.95),  # vivid purple
    (1.00, 0.10, 0.55),  # hot pink
    (0.55, 0.95, 0.10),  # chartreuse
    (0.20, 0.20, 0.20),  # near-black
    (0.95, 0.95, 0.95),  # near-white
    (0.55, 0.30, 0.10),  # rust brown
    (0.10, 0.50, 0.30),  # forest green
]


def generate(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)

    # --- Cube ---
    # Floor at 70 cm and ceiling at 110 cm so the cube fills a clearly
    # visible chunk of frame in 640×480 screenshots from the auto-framed
    # camera (~20+ pixels even at the far end).
    a = rng.uniform(70.0, 110.0)             # cube side length (cm)
    cube_mass = rng.uniform(1.0, 20.0)
    cube_color = _jitter((0.10, 0.30, 1.00), rng)  # blue-ish

    # --- Platform: always strictly larger than cube footprint ---
    blx = a + rng.uniform(80.0, 240.0)
    bly = a + rng.uniform(80.0, 240.0)
    blz = rng.uniform(50.0, 150.0)
    bx  = rng.uniform(-200.0, 200.0)
    by  = rng.uniform(-200.0, 200.0)
    bz  = blz / 2.0                          # platform sits on ground
    plat_color = _jitter((1.00, 0.90, 0.10), rng)  # yellow-ish

    # --- Cube starts off the platform on the ground ---
    a_start_x = bx + blx / 2 + rng.uniform(100.0, 400.0)
    a_start_y = by + rng.uniform(-100.0, 100.0)
    a_start_z = a / 2.0 + 1.0                # sitting on ground

    # --- Distractors: 2-8 random boxes / spheres, rejection-sampled
    # so their XY footprint doesn't overlap the platform / cube /
    # earlier distractors. Total scene object count = 2 + n_d so
    # n_total ∈ [4, 10]. Mesh type is randomly cube or sphere per
    # distractor — sphere just uses Sphere.Sphere mesh, same scale
    # logic, so its bounding XY = (size, size).
    OVERLAP_MARGIN = 30.0
    SAMPLE_RANGE = 600.0
    placed_aabbs = [
        # platform (center, size in X, size in Y)
        (bx, by, blx, bly),
        # cube starting footprint
        (a_start_x, a_start_y, a, a),
    ]

    def _xy_overlaps(cx, cy, sx, sy):
        for (ox, oy, osx, osy) in placed_aabbs:
            if (abs(cx - ox) < (sx + osx) / 2 + OVERLAP_MARGIN and
                    abs(cy - oy) < (sy + osy) / 2 + OVERLAP_MARGIN):
                return True
        return False

    n_d = rng.randint(2, 8)
    # Pick n_d distinct base colors WITHOUT replacement — guarantees
    # every distractor is a unique hue (and unique vs cube/platform
    # since the palette excludes those hues).
    distractor_colors = rng.sample(_DISTRACTOR_COLORS, n_d)

    distractors: list[dict] = []
    for i in range(n_d):
        # Floor at 70 cm — anything smaller is hard to recognize in
        # the screenshot. Upper bound 120 lets distractors get
        # noticeably larger than the cube.
        d_size = rng.uniform(70.0, 120.0)
        # Try up to 80 placements before giving up.
        d_x = d_y = 0.0
        for _attempt in range(80):
            d_x = bx + rng.uniform(-SAMPLE_RANGE, SAMPLE_RANGE)
            d_y = by + rng.uniform(-SAMPLE_RANGE, SAMPLE_RANGE)
            if not _xy_overlaps(d_x, d_y, d_size, d_size):
                break
        else:
            pass  # room crowded; accept last sample
        placed_aabbs.append((d_x, d_y, d_size, d_size))
        d_z = d_size / 2.0 + 1.0
        # Small per-distractor jitter so the rgb isn't an exact palette
        # match, but the base hue is still distinct from every other.
        d_color = _jitter(distractor_colors[i], rng, amt=0.05)
        # Roughly half cubes / half spheres so the agent can't rely on
        # "any cube must be the target".
        mesh_type = rng.choice(("cube", "sphere"))
        distractors.append({
            "mesh": mesh_type,
            "side": round(d_size, 1),
            "position": [round(d_x, 1), round(d_y, 1), round(d_z, 1)],
            "color": d_color,
        })

    # --- Shuffle OBJ_i → role mapping (0=platform, 1=cube, 2..=distractors) ---
    obj_indices = list(range(2 + n_d))
    rng.shuffle(obj_indices)
    cube_idx = obj_indices.index(1)
    platform_idx = obj_indices.index(0)

    # --- Verifier targets (matches lean's PlaceOnPlatform.expectedZ) ---
    expected_z = bz + blz / 2.0 + a / 2.0
    valid_x_range = [bx - (blx - a) / 2.0, bx + (blx - a) / 2.0]
    valid_y_range = [by - (bly - a) / 2.0, by + (bly - a) / 2.0]

    return {
        "test_id": "place_on_platform",
        "seed": seed,
        "cube": {
            "side": round(a, 1),
            "mass": round(cube_mass, 1),
            "start_position": [round(a_start_x, 1),
                                round(a_start_y, 1),
                                round(a_start_z, 1)],
            "color": cube_color,
            "obj_label": f"OBJ_{cube_idx}",
        },
        "platform": {
            "dimensions": [round(blx, 1), round(bly, 1), round(blz, 1)],
            "position":   [round(bx, 1),  round(by, 1),  round(bz, 1)],
            "color": plat_color,
            "obj_label": f"OBJ_{platform_idx}",
        },
        "distractors": distractors,
        "obj_order": obj_indices,
        "n_total_objects": 2 + n_d,
        "verification": {
            "expected_z": round(expected_z, 1),
            "valid_x_range": [round(valid_x_range[0], 1), round(valid_x_range[1], 1)],
            "valid_y_range": [round(valid_y_range[0], 1), round(valid_y_range[1], 1)],
            "epsilon": 5.0,
            "settle_speed": 5.0,    # cm/s — below this we consider "settled"
            "settle_min_s": 1.0,    # how long it must remain below to declare settled
            "max_settle_s": 10.0,   # cap before we just check final pose
        },
    }


if __name__ == "__main__":
    import json, sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    p = generate(seed)
    print(json.dumps(p, indent=2))
