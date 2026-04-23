# `veriworld.benchmark` — Task Examples

This directory contains the VeriWorld task implementations, organized by **execution paradigm**. Each super-category launches against its own packaged Unreal Engine build.

| Category | Build | Sub-categories |
|---|---|---|
| [`interactive/`](interactive/)    | `PackagedOutput`     | `recognition/`, `navigation/` |
| [`computational/`](computational/) | `PackagedOutput_dev` | `feedback/`, `coding/`        |

See [`../../docs/ACCESS.md`](../../docs/ACCESS.md) for how to obtain the builds and [`../../README.md`](../../README.md) for overall setup.

## How a task is organized

Each leaf task (e.g. `interactive/navigation/mazenavfps/`) contains:

- `harness.py` — the agent loop for this task, imports from `veriworld.common` and `veriworld.infra.*`.
- `generate_params.py` — deterministic seed → task-state generator.
- `README.md` — task description, difficulty axes, scoring.
- `task.md` — agent-facing prompt / instructions.

## How to run

From the repo root:

```bash
pip install -e .
python -m veriworld.benchmark.interactive.navigation.mazenavfps.harness --seed 0
```

Each leaf task's README documents its specific CLI flags and UE launch arguments.

## Available tasks

| Path | Kind | What it tests |
|---|---|---|
| [`interactive/navigation/mazenavfps/`](interactive/navigation/mazenavfps/) | per-tick | Wall-following + goal-seeking in a seeded maze. |
| [`interactive/navigation/tunnel/`](interactive/navigation/tunnel/)         | per-tick | Follow a curved Hermite tunnel to its exit. |
| [`computational/feedback/surface_billiards/`](computational/feedback/surface_billiards/) | per-round parameter | Choose `(v_angle, v_speed)` to launch a ball from a fixed point A over gaussian terrain into a target crater at B. Parameter-only; agent returns scalars + `observation` / `knowledge` text. |
| [`computational/coding/drop_to_target/`](computational/coding/drop_to_target/) | per-round code | Write a Python script that deforms a surface so a ball lands in a target circle. `visual/` ablation uses pure video feedback. |
