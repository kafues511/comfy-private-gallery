@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PY="
if defined GALLERY_PYTHON (
  if exist "%GALLERY_PYTHON%" set "PY=%GALLERY_PYTHON%"
)

if not defined PY call :ReadPythonFromConfig
if not defined PY call :FindVenvPython
if not defined PY call :FindPathPython

if not defined PY (
  echo Python was not found.
  echo Put this folder under ComfyUI\user\
  echo or set GALLERY_PYTHON to python.exe
  echo or add python to PATH.
  pause
  exit /b 1
)

echo Starting gallery on 127.0.0.1:8787
echo Python: !PY!
echo If another gallery is running, it will be stopped.
echo Funnel is never enabled by this script.
echo Keep this window open.
echo.
if "%~1"=="" (
  "!PY!" "%~dp0gallery.py"
) else (
  "!PY!" "%~dp0gallery.py" --root "%~1"
)
echo.
echo Gallery stopped.
pause
exit /b 0

:ReadPythonFromConfig
if not exist "%~dp0config.json" goto :eof
for /f "usebackq delims=" %%I in (`powershell -NoProfile -NonInteractive -Command "try { $p = (Get-Content -Raw -Encoding UTF8 '%~dp0config.json' | ConvertFrom-Json).python; if ($p) { Write-Output $p } } catch { }"`) do (
  if exist "%%~I" set "PY=%%~I"
)
goto :eof

:FindVenvPython
set "DIR=%~dp0"
set /a DEPTH=0
:FindVenvLoop
set /a DEPTH+=1
if !DEPTH! GTR 10 goto :eof
if exist "!DIR!venv\Scripts\python.exe" (
  set "PY=!DIR!venv\Scripts\python.exe"
  goto :eof
)
if exist "!DIR!.venv\Scripts\python.exe" (
  set "PY=!DIR!.venv\Scripts\python.exe"
  goto :eof
)
if exist "!DIR!python_embeded\python.exe" (
  set "PY=!DIR!python_embeded\python.exe"
  goto :eof
)
if exist "!DIR!python_embedded\python.exe" (
  set "PY=!DIR!python_embedded\python.exe"
  goto :eof
)
pushd "!DIR!.." 2>nul
if errorlevel 1 goto :eof
set "NDIR=!CD!\"
popd
if /I "!NDIR!"=="!DIR!" goto :eof
set "DIR=!NDIR!"
goto FindVenvLoop

:FindPathPython
where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py"
  goto :eof
)
where python >nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof
