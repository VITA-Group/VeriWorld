# Example Starters

One folder per shipped task. Every launcher calls the orchestrator
(`veriworld/scripts/run_parallel.py`). All **run knobs live in
`run_defaults.json` at the repo root** — the `.bat` files are
near-empty (just `--task <module>`).

```
example_starters/
├── mazenavfps/
│   ├── harness_structured/
│   │   ├── 1_launch_ue.bat   (OPTIONAL — only for --attach debug)
│   │   ├── 2_run_vp_bf.bat
│   │   └── 2_run_pv_bf.bat
│   └── harness_knowledge/
│       └── 2_run_pv_kn.bat   (no 1_launch_ue — computational engine,
│                             ←  UE restarts per round, --attach N/A)
├── tunnel/
│   └── harness_structured/
│       ├── 1_launch_ue.bat   (OPTIONAL)
│       ├── 2_run_vp_bf.bat
│       └── 2_run_af.bat
└── surface_billiards/
    └── run.bat               (pre-harness-wrap; will move to
                               harness_video/run.bat in a follow-up)
```

The `harness_<descriptor>/` folder mirrors the benchmark-tree harness
wrapper introduced in `veriworld/infra/harness/SKILL.md`. Each harness
is a self-contained experiment design (who owns state memory, LLM
call budget, etc.). Launchers live inside the harness folder so it's
unambiguous which design each `.bat` invokes.

## Where do the knobs live?

**`run_defaults.json`** at repo root (gitignored; copy from
`run_defaults.example.json`):

```json
{
  "builds": {
    "interactive":   "C:/.../PackagedOutput/Windows/demo1.exe",
    "computational": "C:/.../PackagedOutput_dev/Windows/demo1.exe"
  },
  "parallel": {
    "seeds": [0, 1],
    "base_port": 9003,
    "max_instances": 6,
    "width": 640,
    "height": 480
  },
  "tasks": {
    "veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf": {
      "max_steps": 30, "grid_size": 4, "materials": 3
    },
    "veriworld.benchmark.interactive.navigation.tunnel.harness_structured.vp_bf": {
      "max_steps": 50, "tunnel_radius": 80, "colorful": true
    },
    ...
  }
}
```

Precedence: CLI flag > `tasks[<task>]` in the JSON > `parallel` block >
hardcoded fallback. Edit the JSON once, `.bat` files don't need touching.

## Normal flow — fair-comparison parallel

1. `pip install -e .` at the repo root (once).
2. Copy and fill in both config files (once per machine):
   - `model_configs.example.json` → `model_configs.json` (API keys)
   - `run_defaults.example.json` → `run_defaults.json` (build paths + defaults)
3. Double-click the ablation's `.bat`.

The orchestrator reads the two configs, spawns one UE per
`(seed, model)` on distinct ports, runs all jobs concurrently up to
`MAX_INSTANCES`, tears everything down, and writes per-seed artefacts
into `veriworld/results/<task_path>/seed_XXXX_TS/<model>/`.

**No need for `1_launch_ue.bat` in this flow** — the orchestrator
launches its own UEs.

## Debug flow — attach to a hand-launched UE

Useful when iterating on prompts / parsers; avoids the ~30 s UE
startup cost per run.

1. Double-click `1_launch_ue.bat` (keep window open).
2. From a terminal:
   ```
   python -m veriworld.scripts.run_parallel \
       --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf \
       --attach --seeds 0 --models gpt-4.1
   ```

The `.bat` files also forward any trailing args via `%*`, so this works
too:
```
.\example_starters\mazenavfps\harness_structured\2_run_vp_bf.bat --attach --seeds 0 --models gpt-4.1
```

For computational tasks `--attach` is meaningless — UE is restarted
per round by the task itself.

## Outputs

Each run's artefacts land under a hierarchical path that mirrors
`veriworld/benchmark/`, so outputs live next to the task they came
from — task → run → model, three clear levels:

```
veriworld/results/
└── <super>/<sub>/<task>/<ablation>/        ← mirrors veriworld/benchmark/
    └── seed_XXXX_TS/                        ← one "run" (all models for a seed)
        ├── orchestrator.json                ← batch info (status / batch_num / models / seeds)
        ├── <model-a>/                       ← per-model per-seed artefacts
        │   ├── config.json
        │   ├── params.json
        │   ├── step_NNN.png · step_NNN_response.txt · step_NNN_movelog.txt ...
        │   └── episode_summary.json
        ├── <model-b>/
        └── <model-c>/
```

Concrete example after running the MazeNavFPS VP/Bf ablation at seeds 0..1:

```
veriworld/results/interactive/navigation/mazenavfps/harness_structured/
├── README.md                            (auto-mirrored from benchmark tree
│                                         by RunLogger on first write)
└── vp_bf/
    ├── seed_0000_20260419_143012/
    │   ├── orchestrator.json
    │   ├── gpt-4o/ ...
    │   └── qwen-vl-max/ ...
    └── seed_0001_20260419_143012/
        ├── orchestrator.json
        └── ...
```

`orchestrator.json` status goes `starting → running → completed`, or
`interrupted` / `failed` if the orchestrator is Ctrl+C'd or crashes.
Each seed dir is self-contained — no separate `parallel_*/` dir.

For cross-seed aggregation, glob `veriworld/results/<task>/seed_*/<model>/`
and parse `episode_summary.json` / `summary.json` — a trivial post-hoc
script we don't ship by default.
