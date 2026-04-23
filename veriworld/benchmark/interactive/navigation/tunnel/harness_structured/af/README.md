# Tunnel / Aim-and-Fly ablation

**Conditions** (hard-coded):

- `action = AF` — compound single action per turn:
  `{"see": "...", "yaw": N, "pitch": M, "forward": D}`. Harness
  decomposes server-side into `turn` / `move_z` / `forward` commands.
- `info = V` — visual only. Screenshot each turn, no position log,
  no raycast.

This is a **different action space** from Bf, so it has its own
harness (per the ablation-isolation rule in
`veriworld/_docs/ablation_structure.md` / memory).

**CLI knobs**:

```
--seed, --port, --configs, --model, --output-root
--tunnel-radius  80 / 50. Default 80.
--colorful       true / false. Default true.
--max-steps      Default 50.
--initial-yaw    Default: along solution path.
```

## Run

```
python -m veriworld.benchmark.interactive.navigation.tunnel.af --seed 0 --port 9003
```

or use `example_starters/tunnel/2_run_af.bat`.
