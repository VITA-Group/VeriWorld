"""Connectivity oracle for MazeNavFPS.

Pure Python BFS that takes a maze grid produced by
``generate_params.generate()`` and checks that the start cell and the
goal cell lie in the same connected component (open cells, 4-neighbour).

Used by :mod:`run_verify` to sweep the benchmark seed range and certify
every scene is solvable. Matches the connectivity property that
``MazeConnectivity.lean`` proves over the LCG-based reference model.

Design:
- This module is *pure* (no file I/O, no Unreal dependency). Callers pass
  the grid in directly — convenient for unit tests and for feeding to
  Lean as a ground-truth for any externally-generated maze.
- ``generate_params.generate()`` already asserts connectivity at the end
  of DFS carving; we duplicate the check here so Lean and Python agree
  on the verification criterion, and so a user can swap in a third-party
  generator without losing the guarantee.
"""

from __future__ import annotations

from collections import deque
from typing import List, Optional, Sequence, Tuple

Grid = Sequence[Sequence[int]]       # 0 = open cell / carved passage, 1 = wall
Point = Tuple[int, int]              # (row, col) in grid coords


def bfs_path(grid: Grid, start: Point, goal: Point) -> Optional[List[Point]]:
    """Shortest 4-neighbour path from start to goal over open cells.

    Returns ``None`` if no path exists, otherwise the list of cells
    from start (inclusive) to goal (inclusive).
    """
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    if not (0 <= start[0] < rows and 0 <= start[1] < cols):
        return None
    if not (0 <= goal[0] < rows and 0 <= goal[1] < cols):
        return None
    if grid[start[0]][start[1]] != 0 or grid[goal[0]][goal[1]] != 0:
        return None

    parents: dict[Point, Point] = {start: start}
    q: deque[Point] = deque([start])
    while q:
        r, c = q.popleft()
        if (r, c) == goal:
            # Reconstruct
            out: List[Point] = []
            cur = goal
            while True:
                out.append(cur)
                if cur == start:
                    break
                cur = parents[cur]
            out.reverse()
            return out
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if (0 <= nr < rows and 0 <= nc < cols
                    and grid[nr][nc] == 0
                    and (nr, nc) not in parents):
                parents[(nr, nc)] = (r, c)
                q.append((nr, nc))
    return None


def is_connected(grid: Grid, start: Point, goal: Point) -> bool:
    return bfs_path(grid, start, goal) is not None


def check_params(params: dict) -> Tuple[bool, Optional[List[Point]]]:
    """Entry point: given a ``params`` dict from ``generate_params``,
    return ``(ok, path)``. Asserts the stored ``solution_path`` — if
    present — actually resolves on the grid."""
    grid = params["grid"]
    start = tuple(params["start_grid"])
    goal = tuple(params["goal_grid"])
    path = bfs_path(grid, start, goal)
    return path is not None, path


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "params.json"
    with open(path, "r") as f:
        params = json.load(f)
    ok, sol = check_params(params)
    if ok:
        print(f"[PASS] seed={params.get('seed', '?')} "
              f"grid={params['grid_rows']}x{params['grid_cols']}  "
              f"path_len={len(sol) if sol else '?'}")
    else:
        print(f"[FAIL] seed={params.get('seed', '?')}  no start→goal path")
        sys.exit(1)
