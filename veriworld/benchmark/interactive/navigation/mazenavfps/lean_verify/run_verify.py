"""Sweep benchmark seeds and certify every generated maze is solvable.

This is the empirical companion to ``MazeConnectivity.lean``:

* ``MazeConnectivity.lean`` proves, via ``native_decide``, that a
  **reference** LCG-based DFS maze carver produces a connected start→goal
  maze for every seed in a fixed range. That proof is over the Lean
  reference model.
* ``run_verify.py`` runs the **actual** Python ``generate_params.generate``
  over the same seed range and calls ``ground_truth.is_connected`` on
  each produced grid. Any seed that fails is reported with a non-zero
  exit code.

The two together give both a mathematical guarantee (Lean, over a model)
and an empirical guarantee (Python, over the real generator).

Usage::

    python -m veriworld.benchmark.interactive.navigation.mazenavfps.lean_verify.run_verify
    python -m ...lean_verify.run_verify --max-seed 50 --grid-size 4
"""

from __future__ import annotations

import argparse
import sys

from ..generate_params import generate as generate_maze
from .ground_truth import bfs_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-seed", type=int, default=100,
                   help="Sweep seeds in [0, max_seed). Default 100.")
    p.add_argument("--grid-size", type=int, default=None,
                   help="Fix the logical grid size. Default: let the "
                        "generator pick per-seed.")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    failed: list[tuple[int, str]] = []
    for seed in range(args.max_seed):
        try:
            params = generate_maze(seed=seed, grid_size=args.grid_size)
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
                  f"path_len={len(path)}")

    print("=" * 60)
    if failed:
        print(f"FAILED  {len(failed)}/{args.max_seed} seeds are not solvable:")
        for seed, reason in failed[:20]:
            print(f"  seed={seed}: {reason}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")
        return 1
    print(f"PASS    all {args.max_seed} seeds produce a connected start->goal maze.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
