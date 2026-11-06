# MazeNavFPS / V-Sa ablation (pure vision, no history, single action)

**Conditions** (hard-coded):

- `info = V` — agent receives ONLY its current first-person screenshot.
  NO coordinates, NO raycast, NO position log, NO labelled history
  grid. Same minimal info channel as the sibling `v_bf/`. Feedback per
  move is binary BLOCKED/ok.
- `action = Sa` — exactly one free movement command per turn
  (`{"cmd": "forward", "distance": D}` OR
  `{"cmd": "turn", "degrees": A}`). Pairs with `v_bf/` (action
  ablation) and `pv_bf/` (info ablation: drop the history grid).

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
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.v_sa --seed 0 --port 9003
```
