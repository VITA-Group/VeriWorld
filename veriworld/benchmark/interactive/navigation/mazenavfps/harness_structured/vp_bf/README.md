# MazeNavFPS / VP-Bf ablation

**Conditions** (hard-coded in this folder):

- `info = VP` — screenshot + position log with BLOCKED/OPEN annotations.
- `action = Bf` — batch of free movement commands per turn.
- `cardinal = True` — N/E/S/W labels for yaw.

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
python -m veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf --seed 0 --port 9003
```

or use `example_starters/mazenavfps/2_run_vp_bf.bat`.

## To create a new ablation

Copy this folder to a sibling (e.g. `vrp_bf/`), then edit `task.py`:

- Update `SYSTEM_PROMPT` for the new info description.
- Change which condition-dependent branches fire (screenshot, raycast,
  position log, history grid) — the helpers in `.._common` give you the
  building blocks.
- Update `run_id` string + `config.json` ablation label.

Adding a `vrp_bf/__main__.py` mirroring this one's is a few-line copy.
