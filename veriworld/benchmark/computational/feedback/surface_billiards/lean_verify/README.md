# SurfaceBilliards — solvability certificate (simulation-based)

Unlike MazeNavFPS, Tunnel, and DropToTarget, SurfaceBilliards **does
not** follow the "Lean-proved algorithm, Python-executed" pattern.

## Why

The other three tasks have closed-form solvability criteria:

- **MazeNavFPS / Tunnel** — BFS over a discrete grid (graph connectivity).
- **DropToTarget** — ramp + projectile motion (analytic landing distance).

Both admit a `native_decide` Lean proof per seed because the check is
O(1) integer arithmetic or bounded iteration.

SurfaceBilliards is different:

- A ball rolls over a sum of 30+ gaussians. There is no closed-form
  trajectory.
- Deciding whether `(v_angle, v_speed)` lands in target requires
  **numerical simulation** — ~8000 steps per shot.
- `generate_params.generate` already does this: it runs **2000 sampled
  shots** per seed at generation time and only returns a params dict
  when one lands in the target valley. The generator itself is the
  oracle.

Writing a Lean file that approximates the trajectory with a simpler
closed-form model would give a proof, but the proof wouldn't apply to
the real CUDA shader — it would be a guarantee about an approximation,
not the actual task. We chose not to ship such an approximation.

## What's here

| File | What it does |
|---|---|
| [`billiard_ball.slang`](billiard_ball.slang)    | The CUDA compute shader that runs the actual ball dynamics in UE. The *reference physics* that defines "solvable" operationally. |
| [`solvability_check.py`](solvability_check.py)  | Sweeps `generate_params.generate(seed)` over a seed range, confirms every seed returns a `solution` dict where the generator's own 2000-shot simulation found a valid shot. Catches silent fallbacks. |

## What "solvable" means here

A seed is solvable iff `generate(seed)` returns a `solution` dict with
`travel > 0` and `dist_to_target <= target_radius`. This is *empirical*
— the 2000-shot sampling could in principle miss a degenerate seed —
but the risk is bounded: if any seed were unsolvable, it would have a
broader range of failing shots, not a narrow needle the sampler can't
find.

Run the sweep to regenerate the guarantee across a range::

    python -m veriworld.benchmark.computational.feedback.surface_billiards.lean_verify.solvability_check \
        --max-seed 50 --verbose
