# MazeNavFPS / VP-BfEq ablation (equal primitive-move budget)

Equal-move-budget variant of `vp_bf/`, built for the advisor concern that
batch-vs-single differences were confounded by action budget. **Info and
action are identical to `vp_bf` (VP, batch-free, cardinal).** The only
differences, all in `task.py`:

- a `move_budget` parameter (shared budget K of primitive moves);
- the final batch is truncated to the remaining budget;
- the goal is checked after every primitive move;
- the system prompt states: *Every command uses one move from a fixed total
  budget of K moves.*

Pair it with the frozen `vp_sa/` run at `max_steps=K` for the matched single
condition. K per grid (p90 of historical batch-pass primitive moves): grid 3/4/5 = 125/170/210.

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.vp_bf_eq \n    --seed 0 --port 9003 --grid-size 5 --move-budget 210 --max-steps 210
```
