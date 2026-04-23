# Lean Verification API Reference

## Log file format

The engine writes a CSV log to `<task>/lean_verify/log_for_verify.txt`
every simulation frame. Schema is task-specific but by convention:

| Column | Meaning |
|---|---|
| `frame` | Integer frame counter (starting ≥ 1) |
| `elapsed` | Wall-clock seconds since the ball / state was spawned |
| `bx`, `by`, `bz` | Primary tracked coordinate (ball position, agent position, …) |
| `dist_to_target` | Scalar distance to whatever the task measures success against |
| `status` | `"flying"` / `"rolling"` / ... during simulation; a terminal token on last line |

Terminal tokens are uppercase and end-state:

- `LANDED_PASS` — agent's output satisfies the verification criterion.
- `LANDED_FAIL` — criterion violated.
- `TIMEOUT` — simulation ran out of frames.

`run_verify.py` polls this file and considers the **first** terminal
line authoritative. Subsequent frames are ignored.

### Non-CSV logs

A few tasks use JSON lines or plain-text status files (e.g. billiards
writes `# RESULT: PASS\n` on its own line). That's fine — the
contract is "there's a distinctive terminal line that run_verify.py
can grep for". Document the exact format in your task's
`lean_verify/README.md`.

## `run_verify.py` lifecycle

Standard 6-step pattern used by every Lean-verified task:

```python
# 1. Kill any old UE instance
subprocess.run(["taskkill", "/F", "/IM", "demo1.exe"], capture_output=True)

# 2. Launch a fresh UE
proc = subprocess.Popen([UE_EXE, "demo1", "-WebSocketPort=9003", ...])

# 3. Wait for WebSocket ready (loop connect until success)
ws = await wait_for_ws("ws://127.0.0.1:9003", timeout=60)

# 4. Clear stale log, execute the agent's code (or ground_truth.py)
Path(LOG_PATH).unlink(missing_ok=True)
await ws_exec(ws, agent_code, timeout=120)

# 5. Poll the log for a terminal line (max settle_timeout seconds)
result = None
deadline = time.time() + 60
while time.time() < deadline:
    await asyncio.sleep(1)
    if Path(LOG_PATH).exists():
        text = Path(LOG_PATH).read_text()
        if "LANDED_PASS" in text: result = "PASS"; break
        if "LANDED_FAIL" in text: result = "FAIL"; break

# 6. Tear down UE (per-PID kill) and return
subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)])
return result
```

VeriWorld tasks implement this pattern through
`veriworld.infra.computational.engine.ComputationalEngine`, which
encapsulates steps 1–3 and 6. Your `run_verify.py` only has to do
steps 4–5.

## Lean 4 file structure

```lean
/-!
# <TaskName>

## Physics model  (informal narrative)

<Explain the physics / logic so a reviewer can read the Lean without
the shader open beside it.>

## Theorem

<Statement of what's proven — typically "∃ solution such that verify
returns true given canSolve holds">

## Verification criterion

<Exact equation / predicate the agent must satisfy.>
-/

-- Part 1: Executable computation (no Mathlib — pure Lean 4 Float)

structure <Name>Params where
  <param1> : Float
  <param2> : Float
  -- ...
  deriving Repr

/-- Precondition: task is physically solvable for these params. -/
def canSolve (p : <Name>Params) : Bool :=
  p.<param1> > 0 &&
  p.<param2> > 0 &&
  <reachability / feasibility checks>

/-- Oracle: what should the agent's output be?  -/
def expected<Answer> (p : <Name>Params) : <OutputType> :=
  <closed-form or iterative computation>

/-- Judge: does the agent's actual output satisfy the criterion? -/
def verify (p : <Name>Params) (agentOutput : <OutputType>) (eps : Float) : Bool :=
  <comparison to expected<Answer> with tolerance eps>

/-- Seed → Params, deterministic. Mirrors the Python generate_params. -/
def generateInstance (seed : Nat) : <Name>Params :=
  <LCG-style deterministic derivation>

-- Part 2: (Optional) formal theorem

theorem solvable_implies_verifiable
    (p : <Name>Params) (h : canSolve p = true) :
    ∃ answer, verify p answer 1e-3 = true := by
  <proof via Mathlib / intermediate value theorem / etc.>
```

### Why executable Lean instead of just a theorem?

- `def expected<Answer>` can be **run** (`#eval` / `lake env lean`)
  against the same seed the engine uses. This means the agent's
  output can be cross-checked numerically without running the
  simulation — useful for smoke tests and CI.
- `verify` is the same predicate Python can call (via `#eval` emitting
  JSON back, or a Lean-to-Python bridge).
- The optional `theorem` layer adds formal guarantee but isn't
  required for the task to function.

## Connecting Lean to the engine

Two integration levels, pick per task:

1. **Log-only (minimum)**: `run_verify.py` reads the CSV, checks the
   terminal line, returns PASS/FAIL. No Lean actually executed. The
   Lean file exists as a specification / human-readable spec.
2. **Lean cross-check (fuller)**: `run_verify.py` also invokes
   `lake env lean <Name>.lean --run` or similar, passing the seed,
   gets back `expected<Answer>` in JSON, compares to the log's actual
   final position. Used for tasks where you want to validate the
   simulator as well as the agent.

Most tasks start at level 1 and graduate to level 2 once the task
design stabilises.

## Common pitfalls

- **Using Mathlib for Float**. Lean 4 core has `Float.sqrt`, `Float.sin`,
  etc. — use those. Mathlib's `Real` isn't computable and breaks
  `#eval`.
- **`Float` precision drift**. Never compare `Float` with `==` in
  `verify`; always use a tolerance `(a - b).abs < eps`.
- **Seed generator divergence**. The Python `generate_params.py` and
  Lean `generateInstance` must produce bit-identical output for the
  same seed, or the cross-check drifts silently. Pick a simple LCG
  and test both sides against each other.
- **Log file not cleared**. If you don't delete `log_for_verify.txt`
  before each run, `run_verify.py` will read the previous run's
  terminal line as the current result. Always clean it at step 4.

## References

- Lean 4 language: <https://lean-lang.org/lean4/doc/>
- Pure-Float utilities (no Mathlib required): `Float.sqrt`, `Float.sin`,
  `Float.cos`, `Float.exp`, `Float.log` — see the Lean 4 stdlib docs.
- Minimal worked example: [`examples/hello_world.lean`](examples/hello_world.lean)
