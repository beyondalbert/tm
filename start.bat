@echo off
rem Launch The Machine (TM) from source on Windows.
rem
rem   start.bat                 # Textual TUI
rem   start.bat --no-tui        # console REPL
rem   start.bat "prompt"        # one-shot agent run
rem   start.bat --login deepseek
rem   start.bat --help
rem
rem Runs via `uv run` against the editable install, so source edits apply
rem immediately and the global `tm` install is left untouched.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV (
  where python >nul 2>nul && set "UV=python -m uv"
)
if not defined UV (
  echo [tm] Could not find "uv" or "python" on PATH.
  echo [tm] Install uv: https://docs.astral.sh/uv/
  exit /b 1
)

if not exist ".venv" (
  echo [tm] First run: installing dependencies...
  !UV! sync --all-extras
  if errorlevel 1 exit /b %errorlevel%
)

!UV! run tm %*
exit /b %errorlevel%
