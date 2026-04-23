# Harness Folder Organization

> **Scope**: how to house **multiple experiment-design conditions** inside
> one VeriWorld task without cross-contamination. This is a folder
> convention, not a Python API.
>
> **Applies to**: every task under `veriworld/benchmark/**`, regardless
> of engine type (interactive or computational).

## Why harnesses need their own folders

A single task (e.g. `mazenavfps`) can be studied under fundamentally
different **experimental designs** — not just different action spaces
or knowledge layouts (which is what Layer-3 ablations already capture),
but different *meta-architectures* for how the agent interacts with the
environment.

Examples:

- **Who owns state memory?** The harness (structured position log,
  deterministic) vs. the agent (free-form `knowledge.md` rewritten each
  step by an extra LLM call, non-deterministic).
- **How is feedback delivered?** Per-tick screenshots vs. end-of-round
  video vs. structured telemetry.
- **How many LLM calls per step?** 1 (pure policy) vs. 2 (policy +
  summarizer) vs. N (chain-of-thought with verifier).

Running the same task under two such designs **in the same folder** —
or having the same `task.py` carry conditional branches — makes it
impossible to look at a result dir and tell which design produced it.
It also tempts authors to share helper code that subtly couples the
two designs together, drifting toward a hybrid that represents neither
design cleanly.

**Rule**: each distinct meta-architecture lives in its own
`harness_<descriptor>/` subfolder. Code, helpers, and documentation
are kept fully isolated. Layer-3 ablations (action space, knowledge
layout) live inside one harness and share its helpers.

## Directory structure

```
<task>/
├── generate_params.py              # scene params — shared across harnesses
├── ue_setup.py · move_camera.py    # UE plumbing — shared
├── task.md · README.md             # task description — shared
├── lean_verify/                    # solvability certificate — shared
├── harness_<descriptor_A>/
│   ├── README.md                   # THIS harness's design (schema below)
│   ├── __init__.py
│   ├── _common.py                  # harness-specific helpers (trackers,
│   │                               #   knowledge summarizers, …)
│   ├── <layer3_ablation_1>/
│   │   ├── task.py
│   │   └── __main__.py
│   └── <layer3_ablation_2>/...
└── harness_<descriptor_B>/
    ├── README.md
    ├── _common.py                  # independent copy — may share utilities
    │                               #   with harness_A by importing from
    │                               #   ``<task>/_shared.py`` only for truly
    │                               #   harness-agnostic helpers (screenshot
    │                               #   IO, wire-format parsing, …)
    └── <layer3_ablation_1>/...
```

**Corresponding result path** (via `task_path_from_module`):

```
veriworld/results/<category>/<subcat>/<task>/harness_<descriptor>/
├── README.md                       # copy of benchmark-side README
│                                   #   (so a raw results dir is self-
│                                   #    documenting — readers don't need
│                                   #    to dig into source to know what
│                                   #    design produced these runs)
└── <layer3_ablation>/
    └── seed_XXXX_YYYYMMDD_HHMMSS/
        └── <model>/ ...
```

## Naming convention

- Folder name: `harness_<short_descriptor>`.
- Descriptor is **1-2 lowercase words** that point at the *principal
  distinguishing axis* (e.g. `structured`, `knowledge`, `rich_feedback`).
- Within one task, all sibling harnesses **must differ along the same
  axis** — don't mix (e.g. one harness separating "memory ownership"
  with another separating "feedback modality"). That collapses into a
  cross-product that's harder to read than a 2D table.
- If a new harness differs along a new axis, pick a new axis name and
  regenerate the sibling names so the axis is clear from the folder
  listing alone.

## Per-harness README schema (required)

Every `harness_<descriptor>/README.md` MUST have these sections, in
this order:

```
# <task> — harness_<descriptor>

## Design principle
One paragraph: what single axis distinguishes THIS harness from its
siblings? What's the claim being tested by isolating it?

## State memory
- **Who owns it?** harness | agent | shared
- **Format**: structured table | free-form text | video | …
- **Update mechanism**: deterministic Python | extra LLM call | none
- **Fed back to agent in next step?** yes (as X) | no (audit only)

## LLM call budget
Per step: <N> call(s). Break down if >1.

## Determinism
- Prompt construction: deterministic | depends on <…>
- State update: deterministic | non-deterministic (LLM summarizer)

## Ablations housed
| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|

## Implementation notes
Anything a reader needs to understand the code (file entry points,
non-obvious coupling, etc.).
```

Follow the schema literally — downstream tooling (and readers skimming
multiple harness READMEs side by side) depends on fixed section names.

## Adding a new harness to an existing task

1. Pick a descriptor. Make sure it names an axis the existing sibling
   harness(es) also differ along — if not, rename everything to the
   new axis.
2. `mkdir <task>/harness_<descriptor>` and add `__init__.py`.
3. Populate the folder by copy-paste from the closest existing harness
   (not by import) — then diverge. Copies are cheaper than shared code
   that drifts.
4. Write `README.md` following the schema above.
5. Add one `<task>/harness_<descriptor>/<ablation>/` for the initial
   Layer-3 ablation. Each ablation has its own `task.py` + `__main__.py`.
6. Update `run_defaults.json` with the new dotted task key
   (`veriworld.benchmark.<…>.<task>.harness_<descriptor>.<ablation>`).
7. Mirror the README into `veriworld/results/<…>/<task>/harness_<descriptor>/README.md`.
8. If the new harness requires shared utilities across its ablations,
   put them in `<task>/harness_<descriptor>/_common.py`. **Do NOT**
   reach back into a sibling harness's `_common.py`; copy what you
   need and evolve independently.

## Anti-patterns

- **Branching task.py on a `--harness` flag**. Collapses the isolation.
- **Shared `_common.py` at task root that serves two harnesses**. Couples
  their helper evolution; one design's refactor breaks the other.
- **Fourth-level harness folders** (`harness_a/harness_b/`). If you need
  two orthogonal axes varied at once, that's a cross product — enumerate
  the combinations explicitly (`harness_a_b`, `harness_a_c`, …), or
  pick one axis as Layer-3 ablations within a single harness.
- **Implicit harness** (task.py at task root with no harness wrapper).
  If a task has exactly one harness today, still wrap it in
  `harness_<descriptor>/` so a future second harness doesn't require a
  breaking move. See `examples/harness_example/` for the minimal shape.
- **Skipping `logger.snapshot_model(...)` in `task.py`**. Without that
  call the per-model run dir gets *no* `config.json`, `reproduce.bat`,
  or `reproduce.sh` — the dir looks fine until someone tries to rerun
  a single model in isolation a week later and discovers there's no
  record of what model + flags + conditions produced it. Both task
  templates (`infra/{interactive,computational}/task_template.py`)
  bake the call into the skeleton; if you wrote your task from scratch
  instead of copying the template, **add the call right after the
  first `logger.model_dir(...)`**. Pass your task's bespoke config
  fields (run_id, ablation tag, condition flags) via the
  ``extra_config`` kwarg so they merge into the standard schema.

## Related

- `veriworld/infra/interactive/engine.py` / `task_template.py` — per-tick
  loop framework, harness-agnostic.
- `veriworld/infra/computational/engine.py` / `task_template.py` —
  per-round restart framework, harness-agnostic.
- `veriworld/benchmark/interactive/navigation/README.md` — family-level
  listing of current harnesses under navigation tasks.
