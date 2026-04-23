"""Seeded generator for DropToTarget.

Byte-for-byte mirror of ``DropToTarget.lean``'s ``generateInstance``:

    s1 = lcg(seed);  surfaceZ = 350 + (s1 mod 150)      ∈ [350, 499]
    s2 = lcg(s1);    angle    = (s2 mod 628) / 100      ∈ [0.00, 6.27]
    s3 = lcg(s2);    dist     = 150 + (s3 mod 200)      ∈ [150, 349]
    s4 = lcg(s3);    targetR  = 40  + (s4 mod 40)       ∈ [40, 79]
    lcg(s) = (s * 1103515245 + 12345) mod 2^31

Lean proves per-seed solvability via ``checkSeed seed = true``. Because
the Python generator executes the same LCG sequence, the Lean proof
transfers: for every seed covered by a ``seedN_is_solvable`` theorem,
the produced ``(surface_z, target, target_radius)`` admits a tilt angle
that lands the ball inside the target circle.

The previous generator used ``random.Random`` with ``uniform`` draws —
continuous, not amenable to ``native_decide``. The switch to LCG +
integer mod sampling slightly coarsens the distribution (step 1 cm on
surface height, 1/100 rad on angle) but unlocks the Lean proof.
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict

LCG_MULT = 1103515245
LCG_INC = 12345
LCG_MOD = 2 ** 31


def _lcg_step(s: int) -> int:
    return (s * LCG_MULT + LCG_INC) % LCG_MOD


def generate(seed: int = 0) -> Dict:
    """Produce a DropToTarget params dict. LCG-sampled; matches Lean."""
    s1 = _lcg_step(seed)
    s2 = _lcg_step(s1)
    s3 = _lcg_step(s2)
    s4 = _lcg_step(s3)

    surface_z = 350.0 + float(s1 % 150)
    angle = float(s2 % 628) / 100.0
    dist = 150.0 + float(s3 % 200)
    target_radius = 40.0 + float(s4 % 40)

    ball_x = 0.0
    ball_y = 0.0
    target_x = round(ball_x + dist * math.cos(angle), 4)
    target_y = round(ball_y + dist * math.sin(angle), 4)
    target_z = 15.0  # ground level

    params = {
        "seed": seed,
        "surface_z": surface_z,
        "ball_start": [ball_x, ball_y, surface_z + 25.0],
        "target": [target_x, target_y, target_z],
        "target_radius": target_radius,
        "grid_n": 40,
        "grid_spacing": 12.0,
        "ball_radius": 15.0,
    }

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "params.json")
    with open(out, "w") as f:
        json.dump(params, f, indent=2)
    return params


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    p = generate(seed)
    print(f"Generated (seed={seed}):")
    print(f"  Surface Z={p['surface_z']}")
    print(f"  Ball start={p['ball_start']}")
    print(f"  Target={p['target']}, radius={p['target_radius']}")
