# Tunnel / VP-Bf ablation

**Conditions** (hard-coded):

- `info = VP` — screenshot + position log with BLOCKED/OPEN annotations.
- `action = Bf` — batch of free 3D movement commands: `forward`,
  `backward`, `turn`, `move_z`.
- `cardinal = True` — N/E/S/W labels in the explored-positions map.

**CLI knobs** (all `generate_params`-level, don't change the agent loop):

```
--seed, --port, --configs, --model, --output-root
--tunnel-radius  80 (large hole) / 50 (small hole). Default 80.
--colorful       true (12 materials) / false (uniform brick). Default true.
--max-steps      Default 50.
--initial-yaw    Override start facing (deg). Default: along solution path.
```

## Run

```
python -m veriworld.benchmark.interactive_control.tunnel_nav3d.vp_bf --seed 0 --port 9003
```

or use `example_starters/tunnel/2_run_vp_bf.bat`.
