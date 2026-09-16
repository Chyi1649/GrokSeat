@echo off
setlocal
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set ROOT=%~dp0..
set PYTHONPATH=%ROOT%\src;%PYTHONPATH%
where python >nul 2>&1
if errorlevel 1 (
  py -3 "%ROOT%\src\grctl.py" %*
) else (
  python "%ROOT%\src\grctl.py" %*
)
