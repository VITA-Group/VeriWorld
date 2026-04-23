"""Seeded maze generator for MazeNavFPS.

This is a **byte-for-byte mirror** of ``lean_verify/MazeConnectivity.lean``'s
``generateGrid``: same LCG (``(s * 1103515245 + 12345) mod 2^31``), same
neighbour iteration order (up / down / left / right), same DFS carving.

Why this matters
----------------
The Lean file proves per-seed connectivity of the reference DFS carver
with ``native_decide``. Because the Python generator below executes the
*same algorithm*, the Lean proof transfers: for every seed covered by a
Lean theorem, the grid Python produces is guaranteed to have a
start → goal path — no runtime BFS is required.

Regression: ``lean_verify/cross_check.py`` holds snapshots of the first
few seeds produced by this file. If anything in the LCG or the
neighbour-scan order drifts from Lean, ``cross_check`` will fail fast.

Default grid size is 4; extend Lean with more ``seedN_NxN_connected``
theorems before introducing seeds or grid sizes not yet covered.
"""

from __future__ import annotations

import json
import math
import os
from collections import deque
from typing import Dict, List, Optional, Tuple

LCG_MULT = 1103515245
LCG_INC = 12345
LCG_MOD = 2 ** 31


def _lcg_step(s: int) -> int:
    return (s * LCG_MULT + LCG_INC) % LCG_MOD


# Order MUST match MazeConnectivity.lean's ``neighbour`` function:
#   idx 0: up (-1, 0), idx 1: down (1, 0), idx 2: left (0, -1), idx 3: right (0, 1)
_DIRS: Tuple[Tuple[int, int], ...] = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _unvisited_neighbours(
    logical_rows: int,
    logical_cols: int,
    visited: List[List[bool]],
    cr: int,
    cc: int,
) -> List[Tuple[int, int, int, int]]:
    acc: List[Tuple[int, int, int, int]] = []
    for dr, dc in _DIRS:
        nr, nc = cr + dr, cc + dc
        if 0 <= nr < logical_rows and 0 <= nc < logical_cols and not visited[nr][nc]:
            acc.append((nr, nc, dr, dc))
    return acc


def _carve_lcg_dfs(logical_rows: int, logical_cols: int, seed: int) -> List[List[int]]:
    """DFS carve with LCG neighbour pick. Mirrors ``generateGrid`` in Lean."""
    rows = 2 * logical_rows + 1
    cols = 2 * logical_cols + 1

    # grid[r][c]: 1 = wall, 0 = open
    grid: List[List[int]] = [[1] * cols for _ in range(rows)]
    visited: List[List[bool]] = [[False] * logical_cols for _ in range(logical_rows)]

    stack: List[Tuple[int, int]] = [(0, 0)]
    rng = seed + 1
    visited[0][0] = True
    grid[1][1] = 0  # open the (0,0) logical cell

    while stack:
        cr, cc = stack[-1]
        nbrs = _unvisited_neighbours(logical_rows, logical_cols, visited, cr, cc)
        if not nbrs:
            stack.pop()
            continue

        pick = rng % len(nbrs)
        rng = _lcg_step(rng)
        nr, nc, dr, dc = nbrs[pick]

        visited[nr][nc] = True
        # Carve wall between (cr, cc) and (nr, nc) — grid coord (2·cr+1 + dr, 2·cc+1 + dc).
        grid[(2 * cr + 1) + dr][(2 * cc + 1) + dc] = 0
        grid[2 * nr + 1][2 * nc + 1] = 0
        stack.append((nr, nc))

    return grid


def _bfs_path(
    grid: List[List[int]],
    start: List[int],
    goal: List[int],
) -> Optional[List[List[int]]]:
    """Shortest open-cell path from start to goal for harness use. Derived
    value, not part of the Lean claim — the Lean theorem already implies
    this path exists; BFS just recovers it for camera initialisation."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    q: deque = deque([(tuple(start), [list(start)])])
    seen = {tuple(start)}
    while q:
        (r, c), path = q.popleft()
        if [r, c] == goal:
            return path
        for dr, dc in _DIRS:
            nr, nc = r + dr, c + dc
            if (0 <= nr < rows and 0 <= nc < cols
                    and grid[nr][nc] == 0
                    and (nr, nc) not in seen):
                seen.add((nr, nc))
                q.append(((nr, nc), path + [[nr, nc]]))
    return None


def generate(seed: int = 0, grid_size: Optional[int] = None) -> Dict:
    """Produce a maze params dict. The DFS-carved grid is Lean-proved
    connected for every seed listed in ``MazeConnectivity.lean``.

    Parameters
    ----------
    seed : int
        Seeds the LCG. Must match one of the ``seedN_*_connected`` theorems
        (or have its theorem added) for the static proof to apply.
    grid_size : int, optional
        Logical cells per side. Defaults to 4 — the benchmark's standard
        size, with Lean coverage in ``MazeConnectivity.lean``.
    """
    import builtins

    if grid_size is not None:
        logical_rows = logical_cols = grid_size
    elif hasattr(builtins, "_MAZE_GRID_SIZE"):
        logical_rows = logical_cols = builtins._MAZE_GRID_SIZE
    else:
        logical_rows = logical_cols = 4

    rows = 2 * logical_rows + 1
    cols = 2 * logical_cols + 1

    grid = _carve_lcg_dfs(logical_rows, logical_cols, seed)

    # Start = top-left logical cell, Goal = bottom-right logical cell.
    start_grid = [1, 1]
    goal_grid = [2 * logical_rows - 1, 2 * logical_cols - 1]

    solution_path = _bfs_path(grid, start_grid, goal_grid)
    assert solution_path is not None, (
        f"seed={seed}: BFS could not recover start→goal path — the Lean "
        "algorithm mirror is broken. Run lean_verify/cross_check.py."
    )

    cell_size = 200.0
    wall_height = 400.0

    def grid_to_world(r: int, c: int) -> Tuple[float, float]:
        return (c + 0.5) * cell_size, (r + 0.5) * cell_size

    solution_world = [list(grid_to_world(r, c)) for r, c in solution_path]

    if len(solution_path) >= 2:
        sx, sy = grid_to_world(*start_grid)
        nx, ny = grid_to_world(*solution_path[1])
        initial_yaw = math.degrees(math.atan2(ny - sy, nx - sx))
    else:
        initial_yaw = 0.0

    params = {
        "seed": seed,
        "logical_rows": logical_rows,
        "logical_cols": logical_cols,
        "grid_rows": rows,
        "grid_cols": cols,
        "grid": grid,
        "start_grid": start_grid,
        "goal_grid": goal_grid,
        "solution_path": solution_path,
        "solution_world": solution_world,
        "initial_yaw": initial_yaw,
        "cell_size": cell_size,
        "wall_height": wall_height,
    }

    out_path = os.path.join(os.path.dirname(__file__), "params.json")
    with open(out_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"[generate] seed={seed}, maze={logical_rows}x{logical_cols}, "
          f"grid={rows}x{cols}, solution_len={len(solution_path)}")
    return params


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    generate(seed)
