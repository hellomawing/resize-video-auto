@echo off
setlocal EnableExtensions
title Video Splitter

rem NOTE: keep this file ASCII-only and CRLF-terminated.
rem cmd.exe mis-parses non-ASCII text in .bat files (it seeks the file by
rem byte offset, so UTF-8 lines get cut in half). All Chinese output is
rem produced by video_splitter.py itself, which handles encoding properly.

rem Switch to the script folder so logs stay next to the script.
cd /d "%~dp0"

echo ====================================================================
echo  Video Splitter - lossless video splitting without re-encoding
echo  Script folder: %~dp0
echo ====================================================================

rem Pick a Python launcher: prefer "py -3", fall back to "python".
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto :no_python

rem Show which interpreter is really used - very useful when debugging.
echo [env] launcher : %PY%
set "PYEXE="
for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE echo [env] python   : %PYEXE%
echo.

rem No arguments -> the interactive wizard starts, folder picker included.
rem You can also drag a folder onto this file's icon.
%PY% "%~dp0video_splitter.py" %*
set "CODE=%errorlevel%"

echo.
echo ====================================================================
if "%CODE%"=="0" (
  echo Finished.
) else (
  echo Finished with exit code %CODE%. Non-zero usually means an error.
)
echo Full output was also saved to the log file:
echo   %~dp0video_splitter_log.txt
echo ====================================================================
echo The window will stay open - scroll up to copy anything you need,
echo then close it. Typing exit here also closes it.
echo.

rem Hand the console over to a child shell, so the window never vanishes
rem when the script ends. No key press required.
"%COMSPEC%" /k

rem No endlocal here: it would drop %CODE% and swallow the exit code.
rem setlocal ends by itself when the batch file ends.
exit /b %CODE%

:no_python
echo.
echo [ERROR] Python 3 was not found.
echo Install it from https://www.python.org/downloads/
echo and make sure "Add python.exe to PATH" is checked during setup.
echo.
pause
exit /b 1
