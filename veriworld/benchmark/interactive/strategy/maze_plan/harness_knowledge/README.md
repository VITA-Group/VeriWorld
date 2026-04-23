# maze_plan — harness_knowledge

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

> **Family**: `interactive/strategy/` (per-round code-gen + summarizer,
> NOT the per-tick navigation harness. For the per-tick knowledge-
> accumulation sibling see
> [`mazenavfps/harness_knowledge`](../../../navigation/mazenavfps/harness_knowledge/README.md)
> under `interactive/navigation/` — different axis, different engine,
> different task.)

## Design principle

This harness tests navigation where **the agent owns state memory**
as a free-form natural-language document (`knowledge.md`), and an
**external summarizer LLM call** rewrites that document between
rounds using the agent's thought + verify log + an accumulated
list of confirmed walls and passages.

The distinguishing axis across sibling harnesses is **who owns state
memory**. The sibling `harness_structured` puts this on the
harness side as a deterministic position table; this harness puts
it on the model side as a non-deterministic narrative. Same task,
same seed → same maze; what varies is how the agent's history is
represented going into the next round.

Ported from the legacy
`AxisWorld-benchmark/.../13b_maze_nav_knowledge/agent_harness_visual.py`.

## State memory

- **Who owns it?** agent, via `knowledge.md`
- **Format**: free-form markdown — confirmed walls, confirmed passages,
  partial map, explore-next suggestions, "mistakes to avoid"
- **Update mechanism**: extra LLM turn
  (`update_knowledge` in `_common.py`) reads previous knowledge +
  the latest round's verify log + thought + a harness-extracted
  fact set (from Bresenham wall-crossing trace) and produces a new
  version. Falls back to a deterministic "just the facts" format
  if the summarizer call fails.
- **Fed back to agent in next step?** Yes — prepended to the next
  round's prompt under `# KNOWLEDGE (read this first!)`.

## LLM call budget

Per step: **2** calls on FAIL rounds — policy (video + prompt →
code) plus summarizer (text-only → new `knowledge.md`). On PASS
rounds only the policy call runs; no knowledge update needed
because the run is over.

## Determinism

- **Prompt construction**: deterministic given the accumulated
  `knowledge.md` content and the latest verify log.
- **State update**: **non-deterministic** — the summarizer LLM
  rewrites knowledge.md in its own words each round. The *fact set*
  fed to the summarizer is deterministic (regex-extracted from
  verify.log), but the summarizer's phrasing of that fact set
  drifts run-to-run.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `pv_kn/`  | **info = PV** (video only), **action = code-gen** | Agent sees the bird's-eye video + the knowledge document; writes a Python BFS snippet that the harness wraps in a scene template. |

Future ablations under this harness would vary what goes into the
prompt (e.g. also give the agent a partial symbolic grid) or what
the summarizer sees (e.g. strip the agent thought). They all share
`_common.py`, `task.md`, `api.md`, `example.py`.

## Implementation notes

- Entry point: `pv_kn/task.py::run`. Async coroutine on
  `ComputationalEngine` (per-round UE restart — required because
  every round re-runs a fresh scene from scratch).
- `_common.py` owns:
  - `SCENE_PREFIX` / `SCENE_SUFFIX` — the UE-side scene wrapper that
    consumes the agent's `path` variable, animates a yellow ball
    along it, turns violated walls bright red, and writes a result
    log.
  - `SETUP_OBSERVE_CODE` — Round-0 bird's-eye flyover (no ball).
  - `assemble_scene(agent_code, workspace)` — substitutes per-worker
    paths and wraps the agent's snippet.
  - `verify_waypoints(waypoints, params)` — strict Bresenham
    wall-crossing check; authoritative source of PASS/FAIL, not the
    in-scene final-position check.
  - `update_knowledge(vlm, workspace, trajectory, rows, cols)` — the
    per-round summarizer turn.
  - `extract_facts_from_verify` / `format_partial_map` — deterministic
    scaffolding for the summarizer prompt.
- Task-level shared assets (`generate_params.py`) live at
  `TASK_ROOT = HERE.parent` so the maze layout is identical to
  `harness_structured`'s for any given seed. This is what makes
  cross-harness comparison apples-to-apples.
- `workspace/lean_verify/log_for_verify.txt` is the per-round
  in-scene log (scene writes; harness polls).
  `workspace/waypoints.json` is the scene's serialized path;
  `verify_waypoints` reads it for the strict check.
- The agent's response format — `thought:` + ` ```python ... ``` ` —
  is parsed by `extract_thought` / `extract_agent_code`. Responses
  that omit the code block are recorded as `error="no ```python code
  block found in response"` and the round is skipped.

## Running

Via the orchestrator:

```
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.pv_kn ^
    --seeds 0 --models gemini-2.5-flash-video
```

Video-capable models are strongly preferred — Round 0's observation
is a video, and Rounds 2+ also hand the agent a replay of its
previous round. With a non-video model the harness degrades to
text-only prompts (the video parameter is dropped), which severely
handicaps the agent.

Solo debug (no fair-compare):

```
python -m veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.pv_kn ^
    --exe "C:\path\to\PackagedOutput_dev\Windows\demo1.exe" ^
    --seed 0 --model gemini-2.5-flash-video
```
