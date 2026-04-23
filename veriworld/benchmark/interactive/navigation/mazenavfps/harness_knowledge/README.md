# mazenavfps — harness_knowledge

> Follows the convention at
> [`veriworld/infra/harness/SKILL.md`](../../../../../infra/harness/SKILL.md).

## Design principle

Same task, same engine (`InteractiveEngine`), same action space, same
scene — **only what the agent sees as its "memory" differs** between
this harness and its sibling `harness_structured`.

- `harness_structured` feeds back **structured world facts**:
  `(x, y): N=BLOCKED, E=OPEN, …` extracted by a deterministic Python
  tracker from the UE movement log.
- `harness_knowledge` ALSO includes that structured map, but
  **appends** a running narrative — the agent's own `thought`,
  the `moves` it submitted, and the `results` it got back — for every
  prior step, concatenated into a `**Accumulated knowledge:**` block.

The distinguishing axis is **what kind of memory the harness shows
the agent**: structured world-state only, vs. structured world-state
plus its own narrative history. Both are harness-maintained,
deterministic, 1 LLM call per step.

Ported from
`AxisWorld-benchmark/unreal_projects_lean/veriworld/harness_win/
parallel_harness.py::KnowledgeManager`.

## State memory

- **Who owns it?** harness (both components — structured tracker +
  narrative log).
- **Format**: structured table (inherited from `harness_structured`)
  **plus** append-only markdown narrative of per-step
  `{thought, moves, results}`.
- **Update mechanism**: deterministic Python — `PositionTracker.update`
  for the structured map, `KnowledgeManager.update` for the narrative.
  No LLM call.
- **Fed back to agent in next step?** Yes — both blocks are injected
  into the user message, in order: previous move results → current
  position → EXPLORED POSITIONS map → Accumulated knowledge.

## LLM call budget

Per step: **1** — policy call only (same as `harness_structured`).
No summarizer.

## Determinism

- **Prompt construction**: deterministic given the cumulative step
  history.
- **State update**: deterministic — every prompt for the same seed /
  same step history is byte-identical across runs.

## Ablations housed

| Subfolder | Layer-3 variation | One-line description |
|-----------|-------------------|----------------------|
| `vp_bf/`  | **info = VP**, **action = Bf** | Screenshot + position map + accumulated narrative; batched free-form `{forward, turn}` per step. Direct counterpart to `harness_structured/vp_bf`. |

Future ablations under this harness would vary the action space or
the info type (e.g. `pv_kn/` — pure-vision with knowledge) while
keeping the self-narrative feedback. They share `_common.py`
(including its `KnowledgeManager`).

## Implementation notes

- Entry point: `vp_bf/task.py::run`. Async coroutine on
  `InteractiveEngine` (persistent UE, per-tick loop — same as
  `harness_structured`).
- `_common.py` is a **copy** of `harness_structured/_common.py` with
  a `KnowledgeManager` class appended. Per the harness convention
  (`infra/harness/SKILL.md`), siblings duplicate common helpers
  rather than import from each other so the two designs can evolve
  independently.
- `KnowledgeManager.update(step, thought, moves, log_entries)` is
  called after every step's move log is parsed. It writes the
  growing `knowledge.md` to the workspace for post-hoc audit; in-
  memory it also returns `get_text()` for prompt injection.
- The first prompt (step 1) intentionally matches
  `harness_structured/vp_bf`'s step-1 prompt byte-for-byte — the
  narrative block is suppressed when empty so the comparison starts
  from an identical baseline.

## Running

Via the orchestrator:

```
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.vp_bf ^
    --seeds 0 --models gpt-4o
```

Or via the one-click launcher: `example_starters/mazenavfps/harness_knowledge/2_run_vp_bf.bat`.

Solo debug:

```
python -m veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.vp_bf ^
    --exe "C:\path\to\PackagedOutput\Windows\demo1.exe" ^
    --seed 0 --model gpt-4o
```
