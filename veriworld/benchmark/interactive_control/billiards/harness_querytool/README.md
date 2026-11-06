# billiards — harness_querytool

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Built **on top of** `harness_visiongroundtruth` — the agent
receives the same image + same full static physics + same per-ball
ground-truth positions every round.  The added affordance is a
per-round REPL with one tool: `simulate_shot(angle, speed)`.

A simulate runs the **same Chaos physics** that the real shot
would use, then **rolls back** every ball to its pre-simulate
position.  The agent gets the deterministic outcome (what
pocketed, where cue ended up, what cue hit first, event log)
without spending real game state.

This is the **strict capability-gain** ablation: structured info
is held constant vs `visiongroundtruth`; the only added variable
is the simulator tool.  Any improvement attributable to query is
*orthogonal* to the structured-info dimension that
`visionposition` / `visionposition_doublecall` confound.

## State memory

- **Who owns it?** UE for ball physics (persistent across rounds
  per seed); the simulate snapshot is held inside the UE tick
  closure so rollback is robust to host disconnect.
- **Format**: `state.json` (per-round dump, same as siblings),
  `simulate_result.json` (rotating per-query, written by UE,
  polled by host).
- **Update mechanism**: same Chaos physics; simulate path captures
  event stream + final positions and **explicitly restores**
  positions + zeroes velocity for every ball.
- **Fed back to agent in next step?** within-round: query result
  is appended to the conversation; across rounds: settled PNG +
  full state push (same as `visiongroundtruth`).

## LLM call budget

Per round: **1 + up to `max_simulates_per_round`** VLM calls.
Default cap = 3 simulates → worst case 4 calls/round, average
expected ≈ 2-3.  Caller may set `max_simulates_per_round=0` to
collapse to a `visiongroundtruth`-equivalent baseline.

## Determinism

- Prompt construction: deterministic given the prior round's PNG
  + `params.json` + `state.json` + the within-round REPL history.
- State update: deterministic Chaos physics for the real shot;
  simulate uses the exact same code path.  Rollback restores
  position + zeros velocity (existing `K2_SetActorLocation` +
  `SetPhysicsLinearVelocity` pattern, proven on PackagedOutput_dev).

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `shot_code/` | **info = visiongroundtruth + simulate_shot tool**, **action = query \| shoot JSON** | Multi-turn REPL within each round; agent may probe up to N hypothetical shots, then commits one real shot. |

## Implementation notes

- Entry point: `shot_code/task.py::run`. InteractiveEngine
  `attach`-mode.
- Shared scene + simulate code via `_shared.py`
  (`SCENE_SETUP_CODE`, `SHOT_TICK_CODE`, **new** `SIMULATE_TICK_CODE`).
- Per-turn artefacts:
  `round_NN_turn_MM_prompt.txt`, `round_NN_turn_MM_response.txt`.
  Per-round summary: `round_NN_simulates.json` (all probes this
  round), `round_NN_shot.json` (final commit), `round_NN.png`
  (post-shot screenshot), `round_NN_verify.txt`.
- Two parsers in `_shared.py`: `extract_repl_action` accepts both
  `query` and `shoot` shapes (and also tolerates a bare
  `{angle_deg, speed_cm_s}` for sibling-compat).
- VLM calls wrapped in 3-retry exponential-backoff (same as other
  siblings; doubly important here because the call count per
  round is up to 4×).
- Cue-respawn semantics + settle thresholds inherited from
  `_shared.py`.

## Running (solo)

```
"...PackagedOutput_dev/Windows/demo1.exe" demo1 \
    -AudioMixer -WebSocketPort=9003 -windowed \
    -ResX=640 -ResY=480 -ForceRes -nosplash

python -m veriworld.benchmark.interactive_control.billiards.harness_querytool.shot_code \
    --exe "...\PackagedOutput_dev\Windows\demo1.exe" \
    --seed 0 --port 9003 --model gemini-2.5-flash
```
