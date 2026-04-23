# MazeNavFPS — knowledge-accumulation harness

You are solving a maze navigation task by emitting a pathfinding
**Python snippet** each round. Your snippet runs inside a harness
scene template that handles UE scene setup — you only write the
pathfinding logic that sets a ``path`` variable.

## Scene

- A 2D grid maze viewed from a bird's-eye camera (top-down, row 0 at
  top, col 0 at left).
- **Grey cubes** = wall cells (grid value 1).
- **White floor** = passage cells (grid value 0).
- **Green sphere** = start position.
- **Red sphere** = goal position.
- On Round 2+, a **yellow ball** traces your previous path, and walls
  you crossed through turn **bright red**.

## What you control

Each round, write a Python code block that reconstructs the grid from
the video and emits a BFS path. The harness auto-wraps your code in a
scene template that sets up the maze, runs the ball, and verifies the
path against the ground-truth grid.

Your code must set a variable ``path`` — a list of ``(row, col)``
tuples from start to goal.

## Coordinate convention (CRITICAL)

**All coordinates are 0-indexed.** Row 0 = top border of the video;
col 0 = left border. Index as ``my_grid[r][c]`` directly. Do NOT
subtract 1 from anything.

## Response format

```
thought: <describe what you see in the video and how you inferred the grid>

```python
# 1. Reconstruct the grid from what you see in the video
#    (0 = passage, 1 = wall, as a list of lists)
my_grid = [
    [1,1,1,1,1,1,1,1,1],
    [1,0,0,...],
    ...
]

# 2. BFS from start_grid to goal_grid
from collections import deque
def bfs(grid, start, goal):
    ...

path = bfs(my_grid, tuple(start_grid), tuple(goal_grid))
```
```

## Notes

- The harness pre-injects ``grid_rows``, ``grid_cols``, ``cell_size``,
  ``start_grid``, ``goal_grid`` into your scope — do not redefine them.
- If ``path`` has fewer than 2 cells the harness substitutes a
  straight-line fallback just so the animation renders (so you can
  see where your BFS failed). That fallback is NOT considered a valid
  solution; it will verify FAIL.
- On Round 2+ you also receive a ``# KNOWLEDGE`` block — a document
  rewritten each round by an external summarizer that tracks which
  cells have been confirmed as walls vs passages. **Read it first;**
  it's your memory across rounds.
