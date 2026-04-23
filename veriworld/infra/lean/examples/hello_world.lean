/-!
# HelloWorld — minimal VeriWorld Lean verifier

## Task spec (informal)

Given a seed producing a target number `t` in [100, 900] and a
tolerance `eps`, the agent submits a float `guess`. The verifier
accepts iff `|guess - t| < eps`.

## Theorem

There trivially exists a valid `guess` for every `HelloParams` —
namely `t` itself. This is here to show the minimum-viable file
structure, not to demonstrate a deep proof.

## Why this file exists

Copy this into your task's `lean_verify/<TaskName>.lean` and swap in
the real physics / geometry. The four sections below are the
skeleton every Lean-verified VeriWorld task needs:

1. `Params` — seed-derived constants
2. `canSolve` — precondition
3. `expected<Answer>` — oracle (what the agent *should* output)
4. `verify` — judge (does the agent's answer satisfy the criterion?)
-/

-- ── 1. Params ─────────────────────────────────────────────────

structure HelloParams where
  target    : Float
  tolerance : Float
  deriving Repr

-- ── 2. canSolve — precondition ────────────────────────────────

/-- A HelloParams is solvable iff the tolerance is positive and the
    target is in the documented range. -/
def canSolve (p : HelloParams) : Bool :=
  p.tolerance > 0.0 && p.target >= 100.0 && p.target <= 900.0

-- ── 3. expected<Answer> — the oracle ──────────────────────────

/-- The oracle answer: for this task the exact target is the answer. -/
def expectedGuess (p : HelloParams) : Float :=
  p.target

-- ── 4. verify — the judge ──────────────────────────────────────

/-- The agent's ``guess`` is correct iff it's within tolerance of target. -/
def verify (p : HelloParams) (guess : Float) : Bool :=
  (guess - p.target).abs < p.tolerance

-- ── 5. generateInstance — seed → Params (mirror Python side) ──

/-- Deterministic LCG-style seed generator. Must match the Python
    ``generate_params.py`` in the task directory. -/
def generateInstance (seed : Nat) : HelloParams :=
  let s1 := (seed * 1103515245 + 12345) % 2147483648
  { target    := 100.0 + Float.ofNat (s1 % 800)   -- [100, 900)
  , tolerance := 1.0
  }

-- ── 6. Driver (for `lake env lean --run`) ─────────────────────

/-- Smoke test: verify that the oracle satisfies the verifier for
    every seed 0..9. -/
def main : IO Unit := do
  for seed in List.range 10 do
    let p := generateInstance seed
    let answer := expectedGuess p
    let ok := verify p answer
    IO.println s!"seed={seed} target={p.target} answer={answer} ok={ok}"

-- To run this file:
--   lake env lean veriworld/infra/lean/examples/hello_world.lean --run
--
-- Expected: every line prints ok=true (the oracle is trivially correct).
-- A real task substitutes real physics for expectedGuess and a real
-- tolerance comparison for verify.
