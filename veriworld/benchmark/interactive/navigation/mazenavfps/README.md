# MazeNavFPS

First-person navigation through a randomly generated grid maze. The
agent explores via per-tick screenshots and movement commands, and must
reach the goal cell before running out of steps.

**Super-category**: interactive / navigation
**Build required**: `PackagedOutput` (see [docs/ACCESS.md](../../../../../docs/ACCESS.md))

## Layout — one folder per ablation

```
mazenavfps/
├── generate_params.py   # shared, pure — seeded DFS maze generator
├── ue_setup.py          # shared — UE-side scene builder
├── move_camera.py       # shared — UE-side per-tick movement loop
├── _common.py           # shared helpers (UE snippets, parsers, tracker, grid)
├── vp_bf/               # ablation: info=VP, action=Bf (screenshot + position log)
│   ├── task.py
│   ├── __main__.py
│   └── README.md
└── pv_bf/               # ablation: info=PV, action=Bf (pure vision history grid)
    ├── task.py
    ├── __main__.py
    └── README.md
```

Each ablation is **self-contained**: hard-coded conditions, its own
`run` function, its own CLI, its own system prompt. No condition
branching, no giant switch statement — just the straight-line code path
for that one combination.

## Adding a new ablation

1. Copy an existing sibling folder (e.g. `vp_bf/`) to a new name
   (e.g. `vrp_bf/`).
2. Edit `task.py`:
   - Update `SYSTEM_PROMPT` to describe the new info/action wording.
   - Adjust which condition-dependent branches fire (screenshot /
     raycast / position log / history grid). Pull whatever you need
     from `.._common`.
   - Change the `run_id` prefix and the `ablation` label in
     `config.json`.
3. Edit `__main__.py` to expose the right CLI flags for your condition.
4. Add an entry under `example_starters/mazenavfps/` if you want a
   double-click `.bat` launcher for it.

That's it — no central registry to update.

## Run

Launch UE on port 9003 first, then run the ablation you want:

```
# VP / Bf (screenshot + position log)
python -m veriworld.benchmark.interactive.navigation.mazenavfps.vp_bf --seed 0

# PV / Bf (pure vision)
python -m veriworld.benchmark.interactive.navigation.mazenavfps.pv_bf --seed 0
```

Or use the launchers under `example_starters/mazenavfps/`.
