@echo off
REM ===========================================================================
REM maze_plan / harness_knowledge / pv_kn — runs via the orchestrator.
REM
REM Knowledge-accumulation harness: 2 LLM calls per round (policy +
REM summarizer). Uses ComputationalEngine (per-round UE restart), so
REM --attach is not supported — no 1_launch_ue.bat under this folder.
REM
REM Prefer a video-capable model (gemini-*-video) so Round 0's
REM bird's-eye observation and Rounds 2+ replay video reach the agent.
REM All knobs live in run_defaults.json (max_rounds, settle_timeout,
REM grid_size, computational build path).
REM ===========================================================================

pushd "%~dp0\..\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.strategy.maze_plan.harness_knowledge.pv_kn %*
popd
pause
