# MazeNavFPS / PV-Bf ablation (pure vision)

**Conditions** (hard-coded):

- `info = PV` — agent receives a labelled history grid of the last N
  screenshots plus the current view. NO coordinates, NO raycast, NO
  position log. Feedback is binary BLOCKED/ok.
- `action = Bf` — batch of free movement commands per turn.

**CLI knobs**:

```
--seed, --port, --configs, --model, --output-root (same as other ablations)
--grid-size      default 4
--materials      default 3
--max-steps      default 30
--history-size   how many past screenshots in the grid (default 6)
--initial-yaw    default: along solution path
```

## Run

```
python -m veriworld.benchmark.interactive.navigation.mazenavfps.pv_bf --seed 0 --port 9003
```

or use `example_starters/mazenavfps/2_run_pv_bf.bat`.
