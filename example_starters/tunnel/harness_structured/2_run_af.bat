@echo off
REM ===========================================================================
REM Tunnel AF (aim-and-fly) — runs via the orchestrator.
REM All knobs live in run_defaults.json.
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.tunnel.harness_structured.af %*
popd
pause
