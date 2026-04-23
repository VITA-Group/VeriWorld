@echo off
REM ===========================================================================
REM MazeNavFPS PV/Bf (pure vision) — runs via the orchestrator.
REM All knobs live in run_defaults.json. See 2_run_vp_bf.bat for debug tips.
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.mazenavfps.harness_structured.pv_bf %*
popd
pause
