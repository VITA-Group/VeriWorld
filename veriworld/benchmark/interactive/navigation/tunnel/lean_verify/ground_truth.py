"""Connectivity oracle for Tunnel.

Thin re-export of the pure BFS used by MazeNavFPS. Tunnel's generator
produces the same grid shape (DFS base + loop openings + dead-end
spurs), so the same oracle applies: BFS from start to goal over
open cells.

Keeping a distinct module here (instead of asking callers to import
from MazeNavFPS) makes ``tunnel.lean_verify`` self-contained and makes
task-specific extensions easy to add later (e.g. if we ever want to
assert additional properties such as "at least one dead-end branch").
"""

from __future__ import annotations

# Shared implementation lives in MazeNavFPS; delegate to it.
from ...mazenavfps.lean_verify.ground_truth import (
    bfs_path,
    is_connected,
    check_params,
)

__all__ = ["bfs_path", "is_connected", "check_params"]


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
        print(f"[FAIL] seed={params.get('seed', '?')}  no start->goal path")
        sys.exit(1)
