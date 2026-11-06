# TunnelNav3D — legacy arms, equal primitive-move budget

This harness reproduces the paper's legacy TunnelNav3D arms — the three action
protocols **AF** (aim-and-fly), **Bf** (batch free) and **Sf** (single free),
each under the two information conditions **VP** (image + position log) and
**VRP** (adds a raycast scan) — on the public release scaffold, under an **equal
primitive-move budget**. It is the harness behind the equal-80-move protocol
study (the paper's tunnel protocol table).

## What is kept verbatim from the paper's legacy harness

The agent-visible path is taken unchanged from the legacy harness
(`harness_win_tunnel/parallel_harness.py`): the AF/Bf/Sf system prompts and the
VP/VRP information blocks, the parsers (`extract_moves_batch`,
`extract_moves_single_free`, `extract_moves_aim_and_fly`, `extract_thought`), the
AF decomposition, the `PositionTracker` (including its non-cardinal yaw keys and
BLOCKED/OPEN counts), the `KnowledgeManager`, the per-turn user message, the
start/goal labels, and the per-call state update. The module docstring lists the
same mapping.

The engine side (level switch, voxel clean, tunnel spawn, scene-ready check,
screenshots, raycast, NAVLOG I/O) uses the release
`veriworld.benchmark.interactive_control.tunnel_nav3d` package. The movement tick
is the release `move_camera.py` with a single batch-logging fix, shipped here as
`move_camera_fixed.py` and read at runtime.

Legacy quirks are preserved deliberately: the VRP prompt says the raycast covers
"4 directions" although the scan uses 12 rays; the AF prompt mentions neither the
map nor the raycast although the VP/VRP user messages carry them; and the map
labels the start (300, 300) although the pawn spawns at (200, 200).

## What changes relative to the legacy arm

- **Equal budget.** Every engine command (`turn`, `move_z`, `forward`,
  `backward`) spends one move from a fixed total budget `K` (the paper's study
  uses `K = 80`). One sentence stating this is appended as the final line of the
  system prompt, identical for all three protocols.
- **Step header.** `**Step k/N** (N-k steps remaining)` becomes
  `**Step k** (R of K moves remaining)`.
- **Budget accounting / stopping.** The run counts primitive commands sent and
  stops at the goal, when the budget is spent (`used >= K`), or at `--max-calls`.
  Bf truncates its command list to the remaining budget; AF truncates after
  decomposition; Sf sends exactly one command.
- **Shorthand sanitizer (Bf/Sf)** and an **AF non-finite guard** make
  non-executable model output fall back to the legacy default move instead of
  stalling the controller; dropped items are recorded in `step_NNN_sanitized.json`.
- **Success test.** A call passes if **any** position logged during the call is
  within 2 x cell (400 cm) of the legacy goal centre (1900, 1900), rather than
  only the call's final position.

## Running

Attach a runtime, then run (one arm, one seed):

```bash
python -m veriworld.benchmark.interactive_control.tunnel_nav3d.harness_structured.legacy_eq \
  --seed 0 --model <model-id> --protocol Bf --info VP --move-budget 80
```

Use `--info VRP` for the raycast condition and `--protocol AF`/`--protocol Sf`
for the other action formats. The pure-Python prompt/parser/budget layer above
`run()` is importable without the engine for offline checks.
