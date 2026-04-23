# tunnel — harness_structured

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Same design principle as `mazenavfps/harness_structured/` — the
harness owns state memory as a deterministic, structured position
log. The distinguishing axis across sibling harnesses is **who owns
state memory** (harness here; future variants may hand it to the
agent).

## State memory

- **Who owns it?** harness
- **Format**: structured table — visited `(x, y)` positions, each
  annotated with per-direction BLOCKED/OPEN status.
- **Update mechanism**: deterministic Python (`PositionTracker.update`).
- **Fed back to agent in next step?** Yes, as an `EXPLORED POSITIONS:`
  block in the user message.

## LLM call budget

Per step: **1** — policy call only.

## Determinism

- **Prompt construction**: deterministic given the move-log history.
- **State update**: deterministic.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `vp_bf/`  | **info = VP**, **action = Bf** | Screenshot + position log. Batched free-form `{forward, turn}` per step. |
| `af/`     | **action = AimFly** (compound) | Same visual input as `vp_bf`, but replaces the batched primitive `forward/turn` with a single compound action per step: `{see, yaw, pitch, forward}` — the agent commits to a full aim-and-fly burst per turn. Tests whether compressing navigation decisions into one compound action helps or hurts vs. tick-level primitives. |

Both ablations share helpers from `_common.py` (PositionTracker, UE
voxel setup for cylindrical tunnel topology, radius/colorful scene
knobs).

## Implementation notes

- Entry points: `vp_bf/task.py::run`, `af/task.py::run`.
- `_common.py` is the tunnel-specific equivalent of mazenavfps's
  structured-harness common file — a distinct PositionTracker
  instance, UE setup code tailored to cylindrical tunnel geometry,
  and the compound-action parser for the `af` variant.
- Task-level shared assets (`ue_setup.py`, `move_camera.py`,
  `generate_params.py`) live at `TASK_ROOT = HERE.parent` and are
  reusable across future harnesses of this task.
- The compound-action `af` ablation still sits under
  `harness_structured` because it only varies the **action space**,
  not the state-memory ownership. A true harness-level fork (e.g.
  agent-authored knowledge) would move to its own sibling folder.

## Running

```
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.tunnel.harness_structured.vp_bf
```

```
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.tunnel.harness_structured.af
```
