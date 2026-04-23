# Tunnel — solvability certificate

Proves every randomised maze produced by Tunnel's generator (DFS base +
LCG-driven loop injection) has a start→goal path. The 3D HermitePipe
that UE carves out of the voxel grid follows the 2D maze path, so 2D
connectivity is sufficient for 3D solvability.

## Design: Lean-proved algorithm, Python-executed

Like MazeNavFPS, Tunnel's `generate_params.py` is a byte-for-byte mirror
of `TunnelConnectivity.lean`. Each runtime seed is covered by a
`bench_seedN_connected` theorem proved with `native_decide` (DFS base +
loop injection, `nLoops = 10`). Drift is caught by `cross_check.py`.

| File | What it does |
|---|---|
| [`ground_truth.py`](ground_truth.py)          | Thin re-export of the pure BFS oracle shared with MazeNavFPS. Kept for general-purpose grid checks; not on the runtime path. |
| [`cross_check.py`](cross_check.py)            | Regression — Python's `_carve_lcg_dfs + _inject_loops` must match Lean's reference snapshots. |
| [`run_verify.py`](run_verify.py)              | Sweeps `generate_params.generate()` across a seed range, BFS-checks each. Smoke test — the Lean proof already implies the sweep passes. |
| [`TunnelConnectivity.lean`](TunnelConnectivity.lean) | Lean 4: LCG DFS + LCG-driven loop injection + per-seed `native_decide` proofs for both the DFS base and the loop-injected runtime output. |

## Run the empirical check

```
python -m veriworld.benchmark.interactive.navigation.tunnel.lean_verify.run_verify \
    --max-seed 100 --grid-size 5
```

Exit code 0 iff every seed in `[0, max_seed)` produces a connected maze
after all post-processing.

## Run the Lean check

```
lean TunnelConnectivity.lean
# or:
lake build
```

## Why the DFS base is enough

The tunnel generator's post-processing — removing interior walls to
create loops, opening spur cells for dead-ends — **only grows** the
open-cell set. Growing the open-cell set of a graph cannot disconnect
any pair that was previously connected. So proving "DFS base is
connected" suffices for "DFS + loops + spurs is connected".

The Lean file proves the DFS base explicitly with `native_decide`, and
also demonstrates the loop-injected variant for a handful of seeds
(redundant given the monotonicity argument, but shows the full
algorithm runs end-to-end).
