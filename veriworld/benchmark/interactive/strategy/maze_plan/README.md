# maze_plan — task

Solve a 2D maze by **writing Python each round** that reconstructs
the grid from a bird's-eye video and emits a BFS path. The harness
wraps the snippet in a UE scene template, animates a ball along the
proposed path, marks violated walls red, and verifies the waypoint
sequence with a Bresenham wall-crossing check.

Uses `ComputationalEngine` (per-round UE restart). Same seeded maze
generator as `interactive/navigation/mazenavfps/` — so a shared seed
produces the same maze under both task families.

See the per-harness README for the full design doc:

- `harness_knowledge/` — agent-authored `knowledge.md` memory,
  rewritten each round by an extra LLM summarizer call.
  [README](harness_knowledge/README.md)
