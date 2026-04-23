# exampletask — harness_example

> Skeleton shape showing the required folder structure + README schema
> that every real harness must follow. Fill each section with the
> actual design of your harness. See
> [`veriworld/infra/harness/SKILL.md`](../../SKILL.md) for the full
> convention.

## Design principle

*(One paragraph.)* Example: "This harness tests whether the agent can
navigate using **only** a screenshot-plus-coordinates table fed by the
harness each step — no agent-authored state. The sibling harnesses
vary along the same axis: **who owns state memory** (harness vs.
agent vs. shared)."

## State memory

- **Who owns it?** harness
- **Format**: structured table (positions visited + BLOCKED/OPEN per
  cardinal direction)
- **Update mechanism**: deterministic Python (`PositionTracker.update`)
- **Fed back to agent in next step?** yes, as `EXPLORED POSITIONS:`
  block in the user message

## LLM call budget

Per step: **1** — just the policy call. No summarizer, no verifier.

## Determinism

- Prompt construction: deterministic given the move log.
- State update: deterministic.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `example_ablation/` | baseline action space | Batched free-form `{forward, turn}` commands, cardinal labels. |

## Implementation notes

- Entry point: `example_ablation/task.py::run`.
- Harness-local helpers live in `_common.py` (PositionTracker, move-log
  parser, prompt-section formatters).
- No dependency on sibling harnesses' code. If a sibling needs the
  same helper, copy it there.
