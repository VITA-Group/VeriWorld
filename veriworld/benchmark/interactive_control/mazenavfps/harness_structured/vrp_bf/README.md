# MazeNavFPS / VRP-Bf ablation

**Conditions** (hard-coded in this folder):

- `info = VRP` — screenshot + 4-direction raycast scan + position log
  with BLOCKED/OPEN annotations.
- `action = Bf` — batch of free movement commands per turn.
- `cardinal = True` — N/E/S/W labels for yaw.

This is the same harness as `vp_bf/`, with one extra channel: a per-turn
raycast (Front / Right / Back / Left wall distance in cm, max 1000)
computed via `KismetSystemLibrary.LineTraceSingle` against the current
pawn pose. The raycast is also dumped into each step's `_movelog.txt`
under the `Raycast (before move):` header for post-hoc audit.

**CLI knobs** (no condition flags — those are fixed):

```
--seed           integer (default 0)
--port           WebSocket port of running UE (default 9003)
--configs        path to model_configs.json (default ./model_configs.json)
--model          name from model_configs.json (default: first entry)
--grid-size      logical cells per side (default 4)
--materials      1/2/3 wall material variants (default 3)
--max-steps      episode length cap (default 30)
--initial-yaw    override start facing in degrees (default: along solution path)
```

## Run (CLI)

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.vrp_bf --seed 0 --port 9003
```
