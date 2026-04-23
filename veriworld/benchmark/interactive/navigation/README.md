# Interactive / Navigation — Task Family

Tasks in this family all use a persistent `InteractiveEngine`, loop
per-tick with agent commands, and measure *spatial reasoning from a
moving first-person viewpoint*.

## Harness convention

This family (and every other VeriWorld task family) follows the
**harness folder convention** documented at
[`veriworld/infra/harness/SKILL.md`](../../../infra/harness/SKILL.md).

Short version: each task folder contains one or more
`harness_<descriptor>/` subfolders, where each harness is a
self-contained experimental design (state-memory ownership, LLM call
budget, prompt wiring, determinism profile). Ablations that vary
inside a single design live as Layer-3 subfolders *within* one
harness; ablations that vary the design itself live as sibling
harnesses.

**Before adding a new harness** to any task in this family, read
`infra/harness/SKILL.md` end-to-end — it defines the naming axis
rules, required README schema, and the anti-patterns to avoid.

## Current harnesses

All harnesses in this family use `InteractiveEngine` (persistent UE,
per-tick loop), image input (screenshot per step), and 1 LLM call per
step. The distinguishing axis is **what kind of memory the harness
shows the agent** — structured world-state only, vs. world-state plus
its own narrative history.

| Task | Harness | Axis value | Notes |
|------|---------|-----------|-------|
| `mazenavfps` | `harness_structured` | structured world-state only (`PositionTracker` with cardinal BLOCKED/OPEN per cell) | Houses `vp_bf` + `pv_bf` Layer-3 ablations. See its [README](mazenavfps/harness_structured/README.md). |
| `mazenavfps` | `harness_knowledge`  | world-state map **+** agent's running self-narrative (every prior step's `thought` + `moves` + `results` concatenated, deterministic) | Houses `vp_bf` Layer-3 ablation. See its [README](mazenavfps/harness_knowledge/README.md). Ported from `AxisWorld-benchmark/.../veriworld/harness_win/parallel_harness.py::KnowledgeManager`. |
| `tunnel`     | `harness_structured` | structured world-state only | Houses `vp_bf` + `af` Layer-3 ablations. See its [README](tunnel/harness_structured/README.md). |

Same seed → same maze across both mazenavfps harnesses (they share
`mazenavfps/generate_params.py`). That's what enables apples-to-apples
comparison: holding the scene + action space fixed, does the agent
benefit from seeing its own prior narrative in addition to the
harness-derived world map?

> **Also see:** `interactive/strategy/maze_plan/` — a **different task
> family** housing a per-round code-generation variant of maze solving
> (agent writes a full BFS snippet per round, with an extra LLM
> summarizer call between rounds). That harness-type is architecturally
> too different to sit alongside the per-tick navigation harnesses here
> and lives in its own family. See
> [`interactive/strategy/README.md`](../strategy/README.md).

Future harnesses (follow the schema at `infra/harness/SKILL.md`) might
move along further points on this axis, or introduce a new axis
entirely (e.g. feedback modality, action granularity) — in which case
sibling descriptors should be renamed so the axis is clear from the
folder listing alone.

## Task-level structure

Within each task folder:

```
<task>/
├── generate_params.py        # scene params generator — shared
├── ue_setup.py               # UE scene build — shared
├── move_camera.py            # per-tick camera loop — shared
├── task.md                   # task description shown to agent — shared
├── README.md                 # task-level overview — shared
├── params.json               # sample seed-0 params — shared
├── lean_verify/              # solvability certificate (Lean/closed-form) — shared
└── harness_<descriptor>/     # one or more — see harness skill
    ├── README.md             # design doc (schema-conformant)
    ├── _common.py            # harness-specific helpers
    └── <ablation>/           # Layer-3 ablation (task.py + __main__.py)
```

The files above the `harness_*` layer are **shared across harnesses**
— the scene is invariant under harness choice by design, so a single
seed generates the same maze/tunnel regardless of which harness
evaluates it. This is what makes cross-harness comparison meaningful.

## Results layout

Mirrors the source layout:

```
veriworld/results/interactive/navigation/<task>/harness_<descriptor>/
├── README.md                 # copy of benchmark-side harness README
│                             #   (mirrored so results dirs are self-
│                             #    documenting without the source tree)
└── <ablation>/seed_XXXX_TIMESTAMP/<model>/...
```
