# Tunnel

3D HermitePipe tunnel carved through a voxel grid of colourful walls.
The agent flies a camera from the green start marker to the red goal
marker along a curving 3D path.

**Super-category**: interactive / navigation
**Build required**: `PackagedOutput` (see [docs/ACCESS.md](../../../../../docs/ACCESS.md))

## Ablations (one folder each)

```
tunnel/
├── generate_params.py   # shared — seeded grid + HermitePipe control points
├── ue_setup.py          # shared — UE-side tunnel + markers spawn
├── move_camera.py       # shared — per-tick 3D movement (supports move_z)
├── _common.py           # shared helpers (UE snippets, parsers, tracker)
├── vp_bf/               # ablation: VP info + batch-free 3D actions
│   ├── task.py
│   ├── __main__.py
│   └── README.md
└── af/                  # ablation: aim-and-fly compound action, vision only
    ├── task.py
    ├── __main__.py
    └── README.md
```

Ablation split rule: parameters `generate_params` controls (tunnel
radius, wall colour, seed, max_steps, initial yaw) are **CLI flags**
shared by both ablations. What differs between folders is the **action
space** — `vp_bf` uses primitive commands (`forward`/`turn`/`move_z`),
`af` uses a compound `{see, yaw, pitch, forward}` that the harness
decomposes. Different action space → different prompt and parser →
different `task.py`.

## Adding more ablations

If you want to vary `info` (e.g. add a `vrp_bf/` with raycast feedback),
copy `vp_bf/` to the new name and edit the system prompt + wire in the
raycast call from `_common.RAYCAST_CODE_3D`. Do NOT just add a flag to
`vp_bf/task.py` — the knowledge-organisation change is exactly the
thing that warrants a new folder.

## Run

```
# VP / Bf
python -m veriworld.benchmark.interactive.navigation.tunnel.vp_bf --seed 0

# Aim-and-Fly
python -m veriworld.benchmark.interactive.navigation.tunnel.af --seed 0
```

Or use the launchers under `example_starters/tunnel/`.
