@echo off
REM ===========================================================================
REM MazeNavFPS VP/Bf — runs via the orchestrator. All knobs live in
REM run_defaults.json at the repo root (seeds, base-port, max-instances,
REM max-steps, grid-size, materials, build path).
REM
REM For debug iteration:
REM   1. Double-click 1_launch_ue.bat, keep window open.
REM   2. From a shell: python -m veriworld.scripts.run_parallel ^
REM        --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf ^
REM        --attach --seeds 0 --models <one-name>
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.vp_bf %*
popd
pause
