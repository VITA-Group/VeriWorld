@echo off
REM ===========================================================================
REM SurfaceBilliards — runs via the orchestrator.
REM All knobs live in run_defaults.json (seeds, max_rounds, build path).
REM --attach is meaningless here (task restarts UE per round).
REM ===========================================================================

pushd "%~dp0\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.computational.feedback.surface_billiards %*
popd
pause
