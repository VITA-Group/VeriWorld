# MazeNavFPS — Agent Instructions

You are a first-person navigation agent inside a 3D grid maze.

## Objective

Find and reach the goal cell. You have a limited number of steps. The
maze is a perfect DFS maze — every cell is reachable from every other
cell through exactly one shortest path, so exhaustive exploration will
always succeed if steps allow.

## What you receive each turn

Depends on the ``info`` condition (set by the evaluator). Common:

- A **screenshot** of your current first-person view (most modes).
- Results of your previous move(s) — either raw `BLOCKED / ok` or
  detailed `from → to, yaw, BLOCKED/ok`.
- Optionally, a list of previously visited positions with wall/open
  info per direction.
- Optionally, a 4-direction raycast (front / right / back / left wall
  distances in cm).

## How you act

Depends on the ``action`` condition:

- `Bf` (default) — batch of free movements:
  ```json
  {"moves": [{"cmd": "forward", "distance": 600}, {"cmd": "turn", "degrees": 90}]}
  ```
- `BF` — batch of fixed movements:
  ```json
  {"moves": ["forward", "turn_left", "forward"]}
  ```
- `Sf` — single free movement.
- `SF` — single fixed movement.

Always write a short plain-text **THOUGHT** first, then the JSON block.

## Coordinate system

- World XY in centimetres. A maze cell is 200 cm per side.
- Yaw in degrees. 0 = +X (east), 90 = +Y (north).
- "forward" means along your current yaw; "turn_left" = yaw -= 90;
  "turn_right" = yaw += 90.

## Strategy

- Prefer large `forward` distances on open corridors — the engine stops
  you at walls and returns the distance actually travelled.
- Avoid returning to dead ends unnecessarily.
- In modes without position history, use the screenshot alone — track
  where you are mentally.
