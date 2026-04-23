# DropToTarget — solvability certificate

Proves every seeded `(surface_z, target, target_radius)` instance admits
a tilt angle that lands the ball inside the target circle.

## Design: Lean-proved algorithm, Python-executed

`generate_params.py` is a byte-for-byte mirror of
`DropToTarget.lean`'s `generateInstance` — same 4-step LCG, same mod
arithmetic. Lean's per-seed `seedN_is_solvable := by native_decide`
theorems therefore transfer to the Python output.

| File | What it does |
|---|---|
| [`DropToTarget.lean`](DropToTarget.lean)          | Lean 4: LCG `generateInstance`, ramp + projectile landing model, `findTiltAngle` bisection, `verify` predicate, per-seed `native_decide` solvability proofs. |
| [`cross_check.py`](cross_check.py)                | Regression — Python `generate()` output must match Lean `generateInstance` snapshots for a few seeds. |
| [`solvability_check.py`](solvability_check.py)    | Pure-Python sweep: for each seed, mirror the Lean physics model and assert a tilt angle exists. Cheap; redundant with the Lean proofs but extends coverage beyond seeds explicitly proved. |
| [`slide_ball.slang`](slide_ball.slang)            | CUDA compute shader — the *actual* physics simulated in UE at task runtime. The Lean ramp+projectile model approximates this. |
| [`ground_truth.py`](ground_truth.py)              | UE-side reference solver — computes the Lean-verified tilt angle and deforms the cloth surface. Runs inside UnrealEditor. |
| [`run_verify.py`](run_verify.py)                  | End-to-end harness: launches UE, runs `ground_truth.py`, watches the log for `LANDED_PASS`. Smoke test for the whole pipeline. |

## Three redundant guarantees

1. **Static Lean proof** — every seed in `seedN_is_solvable` has a
   `native_decide` witness that a tilt angle exists.
2. **Python solvability sweep** — `solvability_check.py` runs the same
   bisection in pure Python over a wider seed range.
3. **UE end-to-end** — `run_verify.py` drives the full shader
   simulation to confirm the Lean model matches the CUDA reality.

Any one is sufficient for confidence; all three together are
belt-and-suspenders.
