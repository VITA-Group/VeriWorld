"""Sweep-based solvability check for SurfaceBilliards.

Confirms that ``generate_params.generate(seed)`` returns a ``solution``
dict with a positive-travel, in-target shot for every seed in a range.

Why this exists
---------------
Billiards does not follow the "Lean-proved algorithm, Python-executed"
pattern the other tasks use, because its solvability check is
inherently simulation-bound — a ball rolling over 30+ summed gaussians
has no closed-form trajectory. Instead, ``generate_params.generate``
itself is the oracle: it runs **2000 sampled shots** per seed and only
returns a params dict when one of those shots lands in the target
valley.

This sweeper confirms that behaviour holds across a seed range. If a
seed's generator fails to find a valid shot (the "WARNING: no valid
shot found" fallback in ``generate_params``), the sweep reports it.

Usage::

    python -m veriworld.benchmark.computational.feedback.surface_billiards.lean_verify.solvability_check
    python -m ...lean_verify.solvability_check --max-seed 50
"""

from __future__ import annotations

import argparse
import math
import sys

from ..generate_params import generate


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-seed", type=int, default=20,
                   help="Sweep seeds in [0, max_seed). Default 20 — each "
                        "seed does a 2000-shot simulation so don't push too high.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    failed: list[tuple[int, str]] = []
    for seed in range(args.max_seed):
        params = generate(seed=seed)
        sol = params["solution"]
        target = params["target"]

        # Fallback path sets dist_to_target=999. Real solutions have
        # dist_to_target <= target_radius.
        if sol["dist_to_target"] > target["radius"]:
            failed.append((seed, f"dist_to_target={sol['dist_to_target']} > R={target['radius']}"))
            continue

        if args.verbose:
            angle_deg = math.degrees(sol["angle"])
            print(f"  seed={seed:3d}  "
                  f"shot=(θ={angle_deg:.0f}°, v={sol['speed']})  "
                  f"dist_to_target={sol['dist_to_target']}")

    print("=" * 60)
    if failed:
        print(f"FAILED  {len(failed)}/{args.max_seed} seeds do not have a verified solution:")
        for seed, reason in failed[:20]:
            print(f"  seed={seed}: {reason}")
        return 1
    print(f"PASS    all {args.max_seed} seeds have a simulation-verified (angle, speed) solution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
