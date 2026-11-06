# mazenavfps — harness_structured / pr_bf

> Sibling of [`vrp_bf`](../vrp_bf/README.md). Same harness, **no
> screenshot**.

## Conditions

| Axis | Value |
|------|-------|
| info | **PR** — Position log + Raycast (no vision) |
| action | **Bf** — batched free `forward` / `turn` |
| state memory owner | harness (`PositionTracker`) |
| LLM call budget per step | 1 (policy only) |

## Why

Tests how much the visual channel was carrying *on top of* the
structured channel that `vrp_bf` already provides. If `pr_bf` matches
or beats `vrp_bf`, the visual was redundant given the same raycast +
position log; if it underperforms, screenshots carry signal the
structured channel misses (e.g., distinguishing visually identical
walls, or wall material cues that the position log doesn't track).

## What changes vs. `vrp_bf`

- No `take_screenshot()` calls (zero in this ablation)
- `messages[0].user.content` is a plain string, not a list of parts
- System prompt removes the "you see a screenshot" line and tells the
  agent it's working from structured feedback only
- `run_id` tag is `..._PR_Bf_...` instead of `..._VRP_Bf_...`

Maze generation, raycast snippet, `PositionTracker`, move execution,
move-log parsing, and the goal-reach verifier are **identical** to
`vrp_bf`.

## Run

Via the orchestrator (reads `run_defaults.json`):

```
python -m veriworld.scripts.run_parallel \
    --task veriworld.benchmark.interactive_control.mazenavfps.harness_structured.pr_bf
```

Or single shot (UE must be running on `--port`):

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.pr_bf \
    --seed 0 --model gemini-3-flash
```
