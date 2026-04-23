@echo off
REM ===========================================================================
REM OPTIONAL — only needed for --attach debug mode.
REM
REM The normal run (2_run_vp_bf.bat / 2_run_pv_bf.bat) uses the orchestrator
REM which launches UEs itself. Use this only when you want to iterate quickly
REM without paying the ~30s UE startup cost per run.
REM
REM Build path is read from run_defaults.json → builds.interactive — do NOT
REM edit a local path here.
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.launch_ue --kind interactive --port 9003
popd
pause
