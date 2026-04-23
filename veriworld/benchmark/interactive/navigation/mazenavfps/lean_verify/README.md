# MazeNavFPS — solvability certificate

Proves every randomised maze produced by the benchmark generator has a
start→goal path.

## Design: Lean-proved algorithm, Python-executed

`generate_params.py` is a **byte-for-byte mirror** of the LCG DFS
algorithm in `MazeConnectivity.lean`. Lean's per-seed `native_decide`
proofs therefore transfer: if `bench_seedN_connected` compiles in Lean,
the maze that Python produces for the same seed + grid_size has a
start→goal path *by construction* — no runtime BFS is needed.

The regression risk is **algorithm drift**: if Python's LCG or DFS
neighbour order diverges from Lean, the proof stops covering Python.
`cross_check.py` pins snapshots of `_carve_lcg_dfs` outputs for a few
seeds so drift fails fast.

| File | What it does |
|---|---|
| [`ground_truth.py`](ground_truth.py)          | Pure Python BFS oracle. Kept as a general-purpose gadget (e.g. for verifying externally-supplied grids) — the main pipeline no longer calls it at runtime. |
| [`cross_check.py`](cross_check.py)            | Regression — Python's LCG DFS must match Lean's reference snapshots. Run after any change to the generator or the Lean algorithm. |
| [`run_verify.py`](run_verify.py)              | Sweeps `generate_params.generate()` across a seed range and confirms every output is connected. Redundant given the Lean proof, but a cheap smoke test. |
| [`MazeConnectivity.lean`](MazeConnectivity.lean) | Lean 4: LCG DFS + BFS + per-seed connectivity proofs for the 3×3, 4×4 (benchmark default), and 5×5 grid sizes. |

## Run the empirical check

```
python -m veriworld.benchmark.interactive.navigation.mazenavfps.lean_verify.run_verify \
    --max-seed 100 --grid-size 4
```

Exit code 0 iff every seed in `[0, max_seed)` produces a connected maze.

## Run the Lean check

```
lean MazeConnectivity.lean        # single-file
# or from a Lake project:
lake build
```

If any `theorem seedN_connected : … := by native_decide` line fails
to compile, the reference carver is broken for that seed and the
connectivity claim is invalidated.

## Why both?

`random.Random` (Python's Mersenne Twister) is impractical to mirror
inside Lean, so Lean proves connectivity over an **LCG reference
carver** — the *class* of DFS perfect mazes, not the specific RNG
stream. The Python oracle swept over the *real* generator closes the
gap empirically.
