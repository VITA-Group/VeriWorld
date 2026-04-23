"""Cheap solvability oracle for DropToTarget.

Mirrors ``DropToTarget.lean``'s ``checkSeed``: for each seed, compute
the tilt angle via 50-step bisection, predict landing position in the
ramp + projectile model, and ``verify`` it falls within the target
circle. This is a pure Python check — no UE or shader needed.

Redundant with the Lean ``theorem seedN_is_solvable`` proofs, but a
useful smoke test across a wider seed range than the Lean file
explicitly proves (which is limited by native_decide time).

Usage::

    python -m veriworld.benchmark.computational.coding.drop_to_target.lean_verify.solvability_check
    python -m ...lean_verify.solvability_check --max-seed 500
"""

from __future__ import annotations

import argparse
import math
import sys

from ..generate_params import generate

# These constants must match slide_ball.slang (CUDA friction / gravity).
GRAVITY = 300.0
FRICTION = 0.4
RAMP_LEN = 120.0
EPS = 10.0


def _landing_distance(surface_z: float, theta: float) -> float:
    sinT = math.sin(theta)
    cosT = math.cos(theta)
    if sinT <= FRICTION:
        return 0.0
    a_net = GRAVITY * (sinT - FRICTION)
    surf_dist = RAMP_LEN / cosT
    v_exit = math.sqrt(2.0 * a_net * surf_dist)
    h_exit = surface_z - RAMP_LEN * math.tan(theta)
    if h_exit <= 0:
        return 0.0
    v_h = v_exit * cosT
    v_z = -v_exit * sinT
    disc = v_z * v_z + 2.0 * GRAVITY * h_exit
    if disc < 0:
        return 0.0
    t_flight = (-v_z + math.sqrt(disc)) / GRAVITY
    return RAMP_LEN + v_h * t_flight


def _find_tilt(surface_z: float, target_d: float) -> float:
    lo, hi = 0.05, 0.75
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if _landing_distance(surface_z, mid) < target_d:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _can_solve(params: dict) -> bool:
    tx, ty, _ = params["target"]
    target_d = math.sqrt(tx * tx + ty * ty)
    if not (
        params["surface_z"] > 0
        and params["target_radius"] > 0
        and target_d > 0
        and target_d < params["surface_z"] * 3.0
    ):
        return False
    theta = _find_tilt(params["surface_z"], target_d)
    d = _landing_distance(params["surface_z"], theta)
    dir_x = tx / target_d
    dir_y = ty / target_d
    lx, ly = d * dir_x, d * dir_y
    hDist = math.sqrt((lx - tx) ** 2 + (ly - ty) ** 2)
    return hDist < params["target_radius"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-seed", type=int, default=100)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    failed: list[tuple[int, str]] = []
    for seed in range(args.max_seed):
        params = generate(seed=seed)
        if not _can_solve(params):
            failed.append((seed, f"target_d={math.sqrt(params['target'][0]**2+params['target'][1]**2):.1f} "
                                 f"surf_z={params['surface_z']}  R={params['target_radius']}"))
            continue
        if args.verbose:
            print(f"  seed={seed:4d}  OK")

    print("=" * 60)
    if failed:
        print(f"FAILED  {len(failed)}/{args.max_seed} seeds not solvable:")
        for seed, reason in failed[:20]:
            print(f"  seed={seed}: {reason}")
        return 1
    print(f"PASS    all {args.max_seed} seeds are solvable under the ramp + projectile model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
