# Lean Verification Skill

## Scope

Formal verification of task correctness using Lean 4. Used by VeriWorld
tasks whose correctness criterion is **mathematically statable** —
typically coding-category tasks where the agent submits executable
code / parameters and the engine must judge pass/fail deterministically.

This is the deepest form of the **solvability certificate** from
Layer 2 of the repo architecture: a machine-checked proof that a valid
solution exists given the seed's parameters. Without it, "the model
failed" is not a claim — the task might have been unsolvable.

## When to use

- The task has a mathematically statable correctness criterion ("the
  returned path visits every cell exactly once and only moves between
  adjacent cells", "the ball lands within R_t of the target", …).
- Floating-point tolerance is insufficient or ambiguous — you want
  existence of a solution proven rather than empirically sampled.
- You want to rule out whole classes of cheating strategies by
  construction.

## When NOT to use

- Perceptual / qualitative judgments ("does this look right") — use
  a scoring rubric plus video review instead.
- Tasks where the simulator *is* the ground truth (e.g. surface
  billiards already has a Slang shader; the log it produces is
  authoritative — no Lean proof needed on top).

## The verification contract

The engine (via the agent's submitted code + a Slang shader) writes a
**log file** — typically
`<task>/lean_verify/log_for_verify.txt` — that records outcome per
frame. The Python `run_verify.py` launches the engine, waits for the
log to contain a terminal marker line (`LANDED_PASS` / `LANDED_FAIL` /
similar), and optionally invokes the Lean 4 file to cross-check the
numeric result against the formal spec.

Log format is task-specific but usually CSV:

```
frame,elapsed,bx,by,bz,dist_to_target,status
10,0.468,0.0,0.0,391.8,302.8,flying
...
300,3.40,108.4,72.1,15.2,2.3,LANDED_PASS
```

See [`api.md`](api.md) for the log-format conventions and
`run_verify.py` lifecycle steps.

## The Lean file structure

Each Lean-verified task's `lean_verify/` ships (at minimum):

1. **`<TaskName>.lean`** — four sections:
   - `structure Params` — the seed-derived physical parameters.
   - `def canSolve : Params → Bool` — precondition checker (refuses
     to run if the task is unsolvable under this seed).
   - `def expected...` — the Lean-side oracle that computes what the
     agent's answer *should* be.
   - `def verify : Params → <agent output> → Bool` — the judge; true
     iff the agent's output satisfies the criterion.
   - Optional formal `theorem` proving `canSolve p → ∃ answer, verify p answer = true`.
2. **`run_verify.py`** — six-step lifecycle: kill UE, launch, connect,
   execute agent code, poll log, read result.
3. **`ground_truth.py`** — a reference solver the agent's code is
   compared against.
4. **`<physics>.slang`** — the compute shader that simulates the task
   deterministically.
5. **`log_for_verify.txt`** — the CSV the engine writes; committed as
   an example of the expected format.

## Skill contents

- [`api.md`](api.md) — log-file CSV schema, `run_verify.py`
  lifecycle, `canSolve` / `verify` patterns, theorem-statement style.
- [`examples/hello_world.lean`](examples/hello_world.lean) — the
  smallest complete Lean-verified task: precondition + oracle +
  verify + a trivial theorem.

## References (shipped in public VeriWorld)

- [`surface_billiards/lean_verify/billiard_ball.slang`](../../benchmark/computational/feedback/surface_billiards/lean_verify/billiard_ball.slang)
  — the physics shader. The Lean proof is a future deliverable — the
  current billiards task uses the shader's PASS/FAIL log alone as its
  solvability certificate.

## References (private R&D tree — useful to mine patterns from)

- `AxisWorld-benchmark/unreal_projects_lean/lean/unit_tests/12c_drop_to_target/lean_verify/DropToTarget.lean`
  — full Lean-proved task: `DropParams` struct, `canSolve` predicate,
  `landingDistance` / `findTiltAngle` oracle, `verify` judge,
  seed → `generateInstance` deterministic generator.
- `unit_tests/13_maze_nav/` — log-only task (no Lean proof); uses the
  same CSV log pattern for pass/fail judgement without the formal
  layer.

## Practical authoring flow

1. Write the physics shader first (Slang). Decide the log schema.
2. Write `ground_truth.py` that computes the expected answer in Python
   from the seed — this is your oracle.
3. Port the oracle to Lean as `def expected...`. Keep it pure
   arithmetic; no I/O.
4. Write `verify : Params → AgentOutput → Bool` — a one-liner that
   compares the agent's answer to the oracle within tolerance.
5. Wrap in `run_verify.py` — launch engine, feed seed, poll log,
   return bool. The Lean proof is optional but recommended for any
   task claiming formal solvability.

See [`examples/hello_world.lean`](examples/hello_world.lean) for the
minimal worked pattern.
