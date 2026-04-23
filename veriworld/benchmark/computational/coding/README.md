# Computational / coding

Per-round tasks where the agent submits **executable code** (full Python scripts, or Slang compute kernels) that the engine runs and verifies against a deterministic correctness criterion.

Distinct from `computational/feedback/`, where the agent submits a **parameter vector** (angle, velocity, timing) into a fixed setup. Here the agent writes the setup itself.

**Build required**: `PackagedOutput_dev` (see [docs/ACCESS.md](../../../../docs/ACCESS.md)).

## Sub-tasks

- [`drop_to_target/`](drop_to_target/) — agent writes a Python script that deforms a GPUClothActor surface so a ball rolls off and lands inside a red target circle on the ground. Verified via Slang physics shader + deterministic pass/fail log.

Coming later: `maze_nav_grid/`, `maze_nav_collide/` (write pathfinding or collision-aware maze nav code).
