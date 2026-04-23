"""Sweep Tunnel benchmark seeds and certify every generated maze is solvable.

Tunnel's ``generate_params.generate()`` produces a DFS perfect maze, then
adds interior-wall openings (~20% of candidates) and dead-end spur cells
(up to 4). Those additions monotonically **grow** the open-cell set, so
they can only preserve or strengthen connectivity — they never
disconnect. BFS from the stored start to the stored goal is therefore a
complete oracle for solvability.

Usage::

    python -m veriworld.benchmark.interactive.navigation.tunnel.lean_verify.run_verify
    python -m ...lean_verify.run_verify --max-seed 50 --grid-size 5
"""

from __future__ import annotations

import argparse
import sys

from ..generate_params import generate as generate_tunnel
from .ground_truth import bfs_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-seed", type=int, default=100,
                   help="Sweep seeds in [0, max_seed). Default 100.")
    p.add_argument("--grid-size", type=int, default=None,
                   help="Fix the logical grid size. Default: generator's 5x5.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    failed: list[tuple[int, str]] = []
    for seed in range(args.max_seed):
        try:
            params = generate_tunnel(seed=seed, grid_size=args.grid_size)
        except AssertionError as e:
            failed.append((seed, f"generator assertion: {e}"))
            continue

        grid = params["grid"]
        start = tuple(params["start_grid"])
        goal = tuple(params["goal_grid"])
        path = bfs_path(grid, start, goal)
        if path is None:
            failed.append((seed, "no path"))
            continue

        if args.verbose:
            print(f"  seed={seed:4d}  {params['grid_rows']}x{params['grid_cols']}  "
                  f"loops={params['loops_added']}  "
                  f"deadends={params['deadends_added']}  "
                  f"path_len={len(path)}")

    print("=" * 60)
    if failed:
        print(f"FAILED  {len(failed)}/{args.max_seed} seeds are not solvable:")
        for seed, reason in failed[:20]:
            print(f"  seed={seed}: {reason}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")
        return 1
    print(f"PASS    all {args.max_seed} seeds produce a connected start->goal maze "
          "(DFS base + loop openings + dead-end spurs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
