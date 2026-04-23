@echo off
REM ===========================================================================
REM OPTIONAL — only needed for --attach debug mode.
REM Build path is read from run_defaults.json → builds.interactive.
REM ===========================================================================

pushd "%~dp0\..\..\.."
python -m veriworld.scripts.launch_ue --kind interactive --port 9003
popd
pause
