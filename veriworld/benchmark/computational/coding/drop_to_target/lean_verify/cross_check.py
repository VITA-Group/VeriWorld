"""Regression check — Python ``generate_params.generate`` must match
``DropToTarget.lean``'s ``generateInstance``.

LCG + mod arithmetic should produce bit-identical output on both sides.
This file pins snapshots; drift fails fast.

Run directly::

    python -m veriworld.benchmark.computational.coding.drop_to_target.lean_verify.cross_check
"""

from __future__ import annotations

import math
import sys
from typing import Dict, List

from ..generate_params import generate


# Snapshot format: {"seed": N, "surface_z": ..., "target": [x, y, z], "target_radius": ...}
# Derived from the same LCG sequence Lean uses (see generateInstance).
SNAPSHOTS: List[Dict] = [
    {"seed": 0,  "surface_z": 395.0, "target_radius": 44.0,
     "target": [-312.4857, -89.318, 15.0]},
    {"seed": 1,  "surface_z": 440.0, "target_radius": 61.0,
     "target": [206.1141, -110.7836, 15.0]},
    {"seed": 42, "surface_z": 377.0, "target_radius": 46.0,
     "target": [-215.0671, 213.4365, 15.0]},
    {"seed": 99, "surface_z": 400.0, "target_radius": 79.0,
     "target": [-315.2609, -21.5999, 15.0]},
]

EPS = 1e-3


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=EPS)


def main() -> int:
    failures: List[str] = []
    for snap in SNAPSHOTS:
        actual = generate(seed=snap["seed"])
        ok = (
            _close(actual["surface_z"], snap["surface_z"])
            and _close(actual["target_radius"], snap["target_radius"])
            and all(_close(a, b) for a, b in zip(actual["target"], snap["target"]))
        )
        if not ok:
            failures.append(
                f"  seed={snap['seed']}:\n"
                f"    expected surf_z={snap['surface_z']}  R={snap['target_radius']}  target={snap['target']}\n"
                f"    actual   surf_z={actual['surface_z']}  R={actual['target_radius']}  target={actual['target']}"
            )

    print("=" * 60)
    if failures:
        print(f"DRIFT  {len(failures)}/{len(SNAPSHOTS)} snapshots mismatch Lean reference.")
        for f in failures:
            print(f)
        return 1
    print(f"PASS   all {len(SNAPSHOTS)} Python LCG snapshots match "
          "DropToTarget.lean's generateInstance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
