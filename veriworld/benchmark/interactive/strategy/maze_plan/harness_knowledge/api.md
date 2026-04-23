# Agent API — knowledge harness

Your code runs inside this scope (pre-injected by the harness):

```python
grid_rows: int          # full grid height (e.g. 9 for a 4×4 logical maze)
grid_cols: int          # full grid width
cell_size: float        # world cm per grid cell
start_grid: list[int]   # [row, col]
goal_grid:  list[int]   # [row, col]
```

You must define:

```python
path: list[tuple[int, int]]  # sequence of (row, col) from start_grid to goal_grid, inclusive
```

Imports you may use:

```python
from collections import deque
import math
import json
import os
```

UE-side libraries (``unreal_runtime``, etc.) are already imported by
the harness wrapper — do not import them yourself and do not touch
UE actors directly. Stay in pure Python logic.

## What the harness does before and after your code

Before:
- Sets up UE scene (grey walls, white floor, green start, red goal).
- Exposes ``grid``, ``grid_rows``, ``grid_cols``, ``cell_size``,
  ``wall_height``, ``start_grid``, ``goal_grid`` as Python variables.

After:
- Converts your ``path`` to world coordinates (via
  ``grid_to_world(r, c) = ((c+0.5)*cell_size, (r+0.5)*cell_size)``).
- Saves waypoints to ``<workspace>/waypoints.json``.
- Animates a yellow ball along the waypoints at 800 cm/s.
- Any wall cell the ball passes through turns bright red.
- When the ball reaches the end of the path, logs
  ``# RESULT: PASS`` or ``# RESULT: FAIL`` based on final-position
  distance to the goal.

A separate Python verifier then runs a strict wall-crossing check
using Bresenham between consecutive waypoints; the result of that
check is what gets reported as the round's verdict.
