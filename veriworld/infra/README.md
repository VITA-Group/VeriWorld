# `veriworld.infra` — Skills + Engine Framework

Everything a new task author needs (on the VeriWorld side). Open one
thing, author one thing — after reading `infra/` you should be able
to write a new task directly into `veriworld/benchmark/`.

The one exception is the **Python-side UE plugin API** (the
`unreal_runtime`-namespaced calls: `ur.Engine.*`, `ur.Voxel.*`,
`ur.submit_tick_task`, etc.). Those live with the UELivePy package
itself — follow whatever that package's docs / SDK update channel
recommends rather than relying on a snapshot in this repo.

## Contents

### Skills (VeriWorld-authored docs)

| Skill | Scope | Who reads it |
|---|---|---|
| [`slang/`](slang/SKILL.md) | Slang compute shaders dispatched via UE's RDG — capability table + what's **out** of scope (no CUDA C, no cuBLAS, no dynamic allocation). | Computational coding / feedback tasks |
| [`lean/`](lean/SKILL.md)   | Lean 4 formal verification harness (for tasks with a mathematically statable correctness criterion). | Tasks requiring formal proofs |

Each skill folder has:
- `SKILL.md` — concept + scope + capability boundary.
- `api.md` — function signatures / message schemas / conventions.
- `examples/` — runnable minimal templates (copy and tweak).

### Engine framework (the runtime your task plugs into)

| Engine | What it manages | Used by |
|---|---|---|
| [`interactive/engine.py`](interactive/engine.py) | Persistent UE instance: launch, WebSocket connect, screenshot, `python_exec`, level switch. Teardown uses `taskkill /F /T /PID` + PID-dead polling to take down the launcher-stub's detached child process tree (plain `terminate()` only kills the stub → accumulates ghost UEs across batches). | `benchmark/interactive/**` tasks |
| [`computational/engine.py`](computational/engine.py) | Per-round UE restart (for clean shader/CUDA state). Same tree-kill + PID-dead sync as interactive, applied every round. | `benchmark/computational/**` tasks |

Each engine folder also has a `task_template.py` — annotated skeleton
with a real ablation example (MazeNavFPS / SurfaceBilliards) inlined as
comments. **To author a new task**, copy the template and fill in.

### UE plugin (Python) API — not in this repo

`ur.Engine.*`, `ur.Voxel.*`, `ur.submit_tick_task`,
`ur.StartRecording`, `ur.event.*`, `ur.log_*`, etc. are provided by
the **UELivePy** UE plugin. Its API evolves with UE versions and
plugin releases, so the canonical docs live with the package, not
here. Read the existing `benchmark/**/task.py` and `ue_setup.py` files
for inline usage examples, and consult the UELivePy package docs for
anything not demonstrated there.

## What is *not* here

- CUDA C / cuBLAS / cuDNN. VeriWorld's coding tasks target UE RDG Slang
  compute only — see [`slang/SKILL.md`](slang/SKILL.md) for scope.
- Unreal Engine C++ or Blueprint authoring. Tasks interact with a
  pre-built engine at runtime; extending the engine itself is not in
  scope.
- Python API reference for the UE plugin itself (see above).

## Authoring flow (recommended)

1. Pick the right engine based on whether your task is per-tick
   (interactive) or per-round (computational).
2. Copy the matching `task_template.py` into
   `veriworld/benchmark/<super>/<sub>/<task>/<ablation>/task.py`.
3. Fill in the three hooks: `generate_params`, `build_prompt`, `run`.
   Reference existing shipped tasks (mazenavfps, tunnel,
   surface_billiards) for UE-side `python_exec` patterns.
4. If your task needs shader physics → read [`slang/SKILL.md`](slang/SKILL.md).
5. If your task needs formal verification → read [`lean/SKILL.md`](lean/SKILL.md).
6. Add a sibling `__main__.py` so `python -m <module>` works, and a
   `README.md` describing the ablation.
