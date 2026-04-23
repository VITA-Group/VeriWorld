# Interactive / Strategy — Task Family

Tasks in this family test **per-round strategic planning** — the
agent submits a complete plan each round (typically as code), the
harness runs it to completion, and feedback comes as end-of-round
video + verifier log. Contrasts with `interactive/navigation/` which
drives UE tick-by-tick.

Uses `ComputationalEngine` (per-round UE restart) even though the
folder sits under `interactive/`, because "interactive" here means
*the agent interacts with the environment* — not *the engine is
per-tick*. Tasks declare this via the `ENGINE = "computational"`
sentinel at the top of their `task.py`; see
`veriworld/scripts/run_parallel.py::_is_computational`.

## Harness convention

Follows the cross-cutting convention at
[`veriworld/infra/harness/SKILL.md`](../../../infra/harness/SKILL.md).
Each task houses one or more `harness_<descriptor>/` subfolders.

## Current tasks

| Task | Harness | Summary |
|------|---------|---------|
| `maze_plan` | `harness_knowledge` | Maze path-planning. Agent sees a bird's-eye flyover video, writes a BFS Python snippet each round, harness wraps it in a scene template and animates a ball along the proposed waypoints; red walls mark violations. Between rounds an **extra LLM summarizer** rewrites `knowledge.md` — the agent's memory document — from the verify log + its own thought. See [maze_plan/README.md](maze_plan/README.md). Ported from `AxisWorld-benchmark/.../13b_maze_nav_knowledge`. |

## When to add a task here vs. under `navigation/`

| Signal | Goes in `strategy/` | Goes in `navigation/` |
|--------|---------------------|-----------------------|
| Agent emits | complete Python plan per round | primitive movement command per tick |
| Feedback cadence | end-of-round video + verify log | per-tick screenshot + move log |
| UE lifecycle | restart each round (clean state) | one persistent instance whole episode |
| Knowledge summarizer | often yes (extra LLM call/round) | rare (deterministic harness-side tracker) |

`navigation/` tests "can the agent close the loop tick-by-tick";
`strategy/` tests "can the agent commit to a full plan from what it's
seen, and iterate based on the outcome".
