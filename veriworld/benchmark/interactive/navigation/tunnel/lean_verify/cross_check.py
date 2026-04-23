"""Regression check — Python tunnel generator must match the Lean reference.

Snapshots of ``_carve_lcg_dfs`` + ``_inject_loops`` output. Drift here
means either Lean ``TunnelConnectivity.lean`` changed (update the
snapshots after re-proving) or Python's LCG/DFS/injection diverged.

Run directly::

    python -m veriworld.benchmark.interactive.navigation.tunnel.lean_verify.cross_check
"""

from __future__ import annotations

import sys
from typing import Dict, List, Tuple

from ..generate_params import (
    DEFAULT_N_LOOP_ATTEMPTS,
    _carve_lcg_dfs,
    _inject_loops,
)


def _gen(rows: int, cols: int, seed: int) -> List[List[int]]:
    g = _carve_lcg_dfs(rows, cols, seed)
    _inject_loops(g, seed, DEFAULT_N_LOOP_ATTEMPTS)
    return g


SNAPSHOTS: Dict[Tuple[int, int, int], List[List[int]]] = {
    (5, 5, 0): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
        [1, 1, 0, 0, 1, 0, 1, 1, 1, 1, 1],
        [1, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1],
        [1, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1],
        [1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
    (5, 5, 1): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
    (5, 5, 42): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 0, 0, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 0, 0, 1, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
}


def main() -> int:
    failures: List[str] = []
    for (rows, cols, seed), expected in SNAPSHOTS.items():
        actual = _gen(rows, cols, seed)
        if actual != expected:
            failures.append(
                f"  ({rows}x{cols}, seed={seed}) mismatch:\n"
                f"    expected row[1]: {expected[1]}\n"
                f"    actual   row[1]: {actual[1]}"
            )

    print("=" * 60)
    if failures:
        print(f"DRIFT  {len(failures)}/{len(SNAPSHOTS)} snapshots do not match.")
        for f in failures:
            print(f)
        print()
        print("Either Lean TunnelConnectivity changed — update SNAPSHOTS — "
              "or Python's LCG DFS / loop injection drifted.")
        return 1
    print(f"PASS   all {len(SNAPSHOTS)} Python LCG snapshots match the Lean reference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
