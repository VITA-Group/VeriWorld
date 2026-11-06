# MazeNavFPS / V-Bf ablation (pure vision, no history)

**Conditions** (hard-coded):

- `info = V` — agent receives ONLY its current first-person screenshot.
  NO coordinates, NO raycast, NO position log, NO labelled history
  grid. Strictly less information than `pv_bf` (which still provides
  the history grid). Feedback per move is binary BLOCKED/ok.
- `action = Bf` — batch of free movement commands per turn
  (`{"cmd": "forward", "distance": D}` and
  `{"cmd": "turn", "degrees": A}`).

The agent must rely entirely on its own context window to remember
where it has been and what it has seen — this isolates the VLM's
pure-spatial-reasoning capability from any harness-side memory aid.

**CLI knobs**:

```
--seed, --port, --configs, --model, --output-root (same as other ablations)
--grid-size      default 4
--materials      default 3
--max-steps      default 30
--initial-yaw    default: along solution path
```

## Run

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.v_bf --seed 0 --port 9003
```
