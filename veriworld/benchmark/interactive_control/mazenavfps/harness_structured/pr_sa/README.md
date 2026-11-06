# mazenavfps — harness_structured / pr_sa

> Sibling of [`pr_bf`](../pr_bf/README.md). Same info channel, **single
> action per turn** instead of batched.

## Conditions

| Axis | Value |
|------|-------|
| info | **PR** — Position log + Raycast (no vision) |
| action | **Sa** — exactly one `forward` / `turn` per turn |
| state memory owner | harness (`PositionTracker`) |
| LLM call budget per step | 1 (policy only) |

## Why

Two-axis ablation against `pr_bf` (action axis) and `vrp_sa` (info
axis):

- vs. `pr_bf` — does forcing one command per turn (more LLM calls per
  unit progress) help or hurt when the agent has only structured
  feedback? Bf agents can plan a multi-step burst before they next see
  the world; Sa agents see a fresh raycast + position log between
  every primitive.
- vs. `vrp_sa` — same single-action regime, but with the visual
  channel stripped: how much of `vrp_sa`'s success was the screenshot
  carrying signal that raycast + position log don't?

## What changes vs. `pr_bf`

- `extract_moves_single_free` instead of `extract_moves_batch`
- System prompt: "Send a BATCH of free movement commands" → "Send
  EXACTLY ONE movement command each turn" (with the single-command
  JSON example)
- `run_id` tag is `..._PR_Sa_...` instead of `..._PR_Bf_...`;
  `ablation` field in `config.json` is `pr_sa`

Maze generation, raycast snippet, `PositionTracker`, move execution,
move-log parsing, and the goal-reach verifier are **identical** to
`pr_bf` and `vrp_sa`.

## Run

Via the orchestrator (reads `run_defaults.json`):

```
python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.interactive_control.mazenavfps.harness_structured.pr_sa
```

Or single shot (UE must be running on `--port`):

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.pr_sa \
    --seed 0 --model claude-sonnet-4-6
```
