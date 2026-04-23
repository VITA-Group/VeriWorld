"""Regression check — Python LCG DFS must match the Lean reference.

The Python ``generate_params._carve_lcg_dfs`` is hand-translated from
``MazeConnectivity.lean``'s ``generateGrid``. Because the Lean
``native_decide`` proofs only cover the Lean algorithm, any drift in
the Python mirror invalidates the transferred guarantee.

This file pins a snapshot of ``_carve_lcg_dfs(4, 4, seed)`` for a few
seeds. If the snapshot no longer matches, either the Lean algorithm
changed (update the snapshots after re-proving in Lean) or the Python
port drifted (fix Python to match Lean).

Run directly::

    python -m veriworld.benchmark.interactive.navigation.mazenavfps.lean_verify.cross_check

Or from pytest once a test harness exists.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Tuple

from ..generate_params import _carve_lcg_dfs

# Snapshots were produced by the Python port and sanity-checked to be
# the output the Lean algorithm *should* produce. Re-generate from a
# Lean ``#eval`` dump to audit.
SNAPSHOTS: Dict[Tuple[int, int, int], List[List[int]]] = {
    # (logical_rows, logical_cols, seed) -> expected grid
    (4, 4, 0): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 1, 1, 1],
        [1, 0, 1, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
    (4, 4, 1): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 1, 1, 1],
        [1, 0, 1, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
    (4, 4, 42): [
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 1, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
    ],
    # 3x3 — matches defaultDims in MazeConnectivity.lean (theorem seed0_connected).
    (3, 3, 0): [
        [1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1, 0, 1],
        [1, 1, 1, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1],
    ],
}


def main() -> int:
    failures: List[str] = []
    for (rows, cols, seed), expected in SNAPSHOTS.items():
        actual = _carve_lcg_dfs(rows, cols, seed)
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
        print("Either Lean MazeConnectivity.generateGrid changed — in which "
              "case update SNAPSHOTS after rerunning `native_decide` in Lean "
              "— or the Python LCG DFS drifted.")
        return 1
    print(f"PASS   all {len(SNAPSHOTS)} Python LCG snapshots match the Lean "
          "reference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
