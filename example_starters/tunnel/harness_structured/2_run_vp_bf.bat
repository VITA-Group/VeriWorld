@echo off
REM ===========================================================================
REM Tunnel VP/Bf — runs via the orchestrator.
REM All knobs live in run_defaults.json (seeds, tunnel_radius, colorful, ...).
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.run_parallel ^
    --task veriworld.benchmark.interactive.navigation.tunnel.harness_structured.vp_bf %*
popd
pause
