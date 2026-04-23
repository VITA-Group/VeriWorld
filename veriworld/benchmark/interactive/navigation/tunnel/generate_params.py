"""Seeded tunnel maze generator.

Byte-for-byte mirror of ``lean_verify/TunnelConnectivity.lean``'s
``generateBaseGrid`` + ``injectLoops``:

1. **DFS base** — identical to MazeNavFPS's LCG DFS carver.
2. **Loop injection** — ``N_LOOP_ATTEMPTS`` iterations. Each iteration
   draws an ``(r, c)`` grid coordinate from the LCG; if the cell is
   interior and currently a wall, flip it open. Missed attempts
   (boundary or already open) are skipped without retry — matches Lean
   byte-for-byte.

The previous Python generator used ``random.Random`` plus ``shuffle``
heuristics (~20 % of interior walls, up to 4 dead-end spurs). Those
don't reduce cleanly to a Lean ``native_decide`` proof, so both sides
now share the simpler LCG-attempt algorithm. The resulting mazes are
connected by construction because (a) the DFS base is connected and
(b) flipping walls to open only grows the open-cell set.

Seeds covered by ``TunnelConnectivity.lean`` theorems transfer directly
to Python output. Use ``lean_verify/cross_check.py`` to detect drift.
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

# Matches ``injectLoops`` in TunnelConnectivity.lean.
DEFAULT_N_LOOP_ATTEMPTS = 10


def _lcg_step(s: int) -> int:
    return (s * LCG_MULT + LCG_INC) % LCG_MOD


# Neighbour order must match Lean's ``neighbour``: up, down, left, right.
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
    """DFS base carve. Identical to mazenavfps's LCG DFS."""
    rows = 2 * logical_rows + 1
    cols = 2 * logical_cols + 1

    grid: List[List[int]] = [[1] * cols for _ in range(rows)]
    visited: List[List[bool]] = [[False] * logical_cols for _ in range(logical_rows)]

    stack: List[Tuple[int, int]] = [(0, 0)]
    rng = seed + 1
    visited[0][0] = True
    grid[1][1] = 0

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
        grid[(2 * cr + 1) + dr][(2 * cc + 1) + dc] = 0
        grid[2 * nr + 1][2 * nc + 1] = 0
        stack.append((nr, nc))

    return grid


def _inject_loops(
    grid: List[List[int]],
    seed: int,
    n_attempts: int = DEFAULT_N_LOOP_ATTEMPTS,
) -> int:
    """Mirror of Lean ``injectLoops``. Returns the number of walls
    actually opened (not every attempt succeeds). Mutates ``grid``."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    # Lean seeds the loop LCG from ``seed * 17 + 3`` — deliberately
    # decoupled from the DFS rng so both phases are independent.
    rng = seed * 17 + 3
    opened = 0
    for _ in range(n_attempts):
        r = rng % rows
        rng = _lcg_step(rng)
        c = rng % cols
        rng = _lcg_step(rng)
        if 0 < r < rows - 1 and 0 < c < cols - 1 and grid[r][c] == 1:
            grid[r][c] = 0
            opened += 1
    return opened


def _bfs_path(
    grid: List[List[int]],
    start: List[int],
    goal: List[int],
) -> Optional[List[List[int]]]:
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


def generate(
    seed: int = 0,
    grid_size: Optional[int] = None,
    n_loop_attempts: int = DEFAULT_N_LOOP_ATTEMPTS,
) -> Dict:
    """Produce a tunnel params dict. DFS + loop injection, both LCG-driven.

    Seeds covered by ``TunnelConnectivity.lean`` theorems produce
    mazes that are connected by construction — no runtime BFS check
    required.
    """
    logical_rows = logical_cols = grid_size if grid_size is not None else 5
    rows = 2 * logical_rows + 1
    cols = 2 * logical_cols + 1

    grid = _carve_lcg_dfs(logical_rows, logical_cols, seed)
    loops_opened = _inject_loops(grid, seed, n_loop_attempts)

    start_grid = [1, 1]
    goal_grid = [2 * logical_rows - 1, 2 * logical_cols - 1]

    solution_path = _bfs_path(grid, start_grid, goal_grid)
    assert solution_path is not None, (
        f"seed={seed}: BFS could not recover start→goal path — the Lean "
        "algorithm mirror is broken. Run lean_verify/cross_check.py."
    )

    # Count open dead-end cells (for stats — not part of the correctness claim).
    dead_end_count = 0
    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            if grid[r][c] == 0 and r % 2 == 1 and c % 2 == 1:
                open_neighbors = sum(
                    1 for dr, dc in _DIRS
                    if grid[r + dr][c + dc] == 0
                )
                if open_neighbors == 1:
                    dead_end_count += 1

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
        "maze_type": "lcg_dfs_with_loops",
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
        "n_loop_attempts": n_loop_attempts,
        "loops_opened": loops_opened,
        # Legacy field retained for any consumer of the previous generator;
        # no longer has a separate generation phase.
        "deadends_added": 0,
        "dead_end_count": dead_end_count,
    }

    out_path = os.path.join(os.path.dirname(__file__), "params.json")
    with open(out_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"[generate] seed={seed}, maze={logical_rows}x{logical_cols}, "
          f"grid={rows}x{cols}, loops_opened={loops_opened}, "
          f"solution_len={len(solution_path)}")
    return params


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    generate(seed)
