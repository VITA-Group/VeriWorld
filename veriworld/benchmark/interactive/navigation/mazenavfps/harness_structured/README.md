# mazenavfps — harness_structured

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

This harness tests navigation where **the harness owns all state
memory** in a structured, deterministic form. Each step the agent
receives a screenshot plus a harness-maintained position log
(`PositionTracker`) that records every cell visited and, for each
visited cell, which cardinal directions were found BLOCKED or OPEN.
The agent never writes to this state; it reads it, plans, and emits
movement commands.

The distinguishing axis across sibling harnesses is **who owns state
memory**. Any future `harness_knowledge/` or similar would differ
along this same axis (agent-authored natural-language memory
summarized each step via an extra LLM call).

## State memory

- **Who owns it?** harness
- **Format**: structured table — visited `(x, y)` positions, each
  annotated `N=BLOCKED/OPEN`, `E=...`, `S=...`, `W=...` (cardinal
  labels when `use_cardinal=True`).
- **Update mechanism**: deterministic Python (`PositionTracker.update`
  consumes the UE-side move log and populates the table).
- **Fed back to agent in next step?** Yes — injected into the user
  message as an `EXPLORED POSITIONS:` block, with the current cell
  flagged `<-- YOU ARE HERE`.

## LLM call budget

Per step: **1** — just the policy call. No summarizer, no verifier,
no chain-of-thought second pass.

## Determinism

- **Prompt construction**: deterministic given the move-log history.
- **State update**: deterministic (pure Python over structured move
  log; no model in the loop).

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `vp_bf/`  | **info = VP**, **action = Bf** | Agent sees screenshot + full position log. Batched free-form `{cmd: forward/turn}` commands each step. |
| `pv_bf/`  | **info = PV**, **action = Bf** | Agent sees *only* a pure-vision history grid (no coordinates). Same Bf action space. Tests what navigation quality looks like when the structured position log is stripped and the agent must rely on visual memory alone. |

Both ablations share helpers from `_common.py` (PositionTracker,
move-log parsing, UE voxel setup templates, screenshot IO wrappers).

## Implementation notes

- Entry points: `vp_bf/task.py::run`, `pv_bf/task.py::run`. Both are
  async coroutines matching the `InteractiveEngine` signature.
- `_common.py` owns the harness-specific state primitives:
  `PositionTracker`, `parse_navlog`, `extract_moves_batch`,
  `extract_thought`, `take_screenshot`.
- Task-level shared assets (`ue_setup.py`, `move_camera.py`,
  `generate_params.py`) are read from `TASK_ROOT = HERE.parent` so
  they stay at the `mazenavfps/` root and are reusable across
  harnesses.
- The agent's `thought` is extracted and saved to
  `step_NNN_thought.txt` for post-hoc audit but is **not** fed back
  to the agent on the next step — this is a deliberate harness design
  choice. A sibling `harness_knowledge/` (if added) would reverse it.

## Running

Via the orchestrator (reads `run_defaults.json`):

```
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf
```

Or via the per-ablation entry point:

```
python -m veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf --seed 0
```
