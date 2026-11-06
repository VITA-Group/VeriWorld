# MazeNavFPS / VRP-BfEq ablation (equal primitive-move budget)

Equal-move-budget VRP twin of `vrp_bf/` (screenshot + raycast + position log,
batch-free, cardinal). Same equal-move edits as `vp_bf_eq/`: a shared
`move_budget` K, the final batch truncated to the remaining budget, the goal
checked after every primitive move, and the budget stated in the prompt.
Pair with the frozen `vrp_sa/` at `max_steps=K`. K per grid = 125/170/210.

```
python -m veriworld.benchmark.interactive_control.mazenavfps.harness_structured.vrp_bf_eq \n    --seed 0 --port 9003 --grid-size 5 --move-budget 210 --max-steps 210
```
