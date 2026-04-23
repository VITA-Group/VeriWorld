# Working example — naive "no walls" BFS. It will fail verification
# (ignores walls) but demonstrates the API shape and variable flow.
from collections import deque


def bfs(grid, start, goal):
    rows = len(grid)
    cols = len(grid[0])
    q = deque([(start, [start])])
    seen = {start}
    while q:
        (r, c), path_so_far = q.popleft()
        if (r, c) == goal:
            return path_so_far
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in seen:
                # Naive: treat every cell as passable. Fix this in your
                # real answer by consulting the video + knowledge doc.
                seen.add((nr, nc))
                q.append(((nr, nc), path_so_far + [(nr, nc)]))
    return [start]


# Assume the full grid is open — placeholder, replace with what you see.
my_grid = [[0] * grid_cols for _ in range(grid_rows)]

path = bfs(my_grid, tuple(start_grid), tuple(goal_grid))
