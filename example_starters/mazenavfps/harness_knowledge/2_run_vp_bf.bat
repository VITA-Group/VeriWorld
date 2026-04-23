@echo off
REM ===========================================================================
REM MazeNavFPS / harness_knowledge / VP+Bf — runs via the orchestrator.
REM
REM Same engine + action space as harness_structured/vp_bf, with an
REM additional **Accumulated knowledge** block in the prompt each
REM step (agent sees its own thought + moves + results history).
REM Still 1 LLM call per step — the accumulation is deterministic.
REM
REM All knobs live in run_defaults.json at the repo root.
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_knowledge.vp_bf %*
popd
pause
