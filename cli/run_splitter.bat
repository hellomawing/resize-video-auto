@echo off
setlocal EnableExtensions
title Video Splitter

rem NOTE: keep this file ASCII-only and CRLF-terminated.
rem cmd.exe mis-parses non-ASCII text in .bat files (it seeks the file by
rem byte offset, so UTF-8 lines get cut in half). All Chinese output is
rem produced by video_splitter itself, which handles encoding properly.

rem Switch to the script folder so logs stay next to the script.
cd /d "%~dp0"

echo ====================================================================
echo  Video Splitter - lossless video splitting without re-encoding
echo  Folder: %~dp0
echo ====================================================================

rem Runner order:
rem   1) packaged executable in dist\  (built by: python build_exe.py)
rem   2) packaged executable next to this file
rem   3) a Python 3 interpreter
rem The first two need no Python at all on this machine.
rem
rem Within dist\ we prefer the onedir layout (dist\video_splitter\video_splitter.exe)
rem when it exists: it starts in about a second, whereas the onefile build has to
rem unpack itself into %TEMP% and delete it again on every run.
if exist "%~dp0dist\video_splitter\video_splitter.exe" goto :use_dist_dir
if exist "%~dp0dist\video_splitter.exe" goto :use_dist
if exist "%~dp0video_splitter.exe" goto :use_here
goto :use_python

:use_dist_dir
echo [env] runner : dist\video_splitter\video_splitter.exe  (standalone, no Python needed)
echo.
"%~dp0dist\video_splitter\video_splitter.exe" %*
set "CODE=%errorlevel%"
goto :done

:use_dist
echo [env] runner : dist\video_splitter.exe  (standalone, no Python needed)
echo.
"%~dp0dist\video_splitter.exe" %*
set "CODE=%errorlevel%"
goto :done

:use_here
echo [env] runner : video_splitter.exe  (standalone, no Python needed)
echo.
"%~dp0video_splitter.exe" %*
set "CODE=%errorlevel%"
goto :done

:use_python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto :no_runner

echo [env] runner : %PY% video_splitter.py
set "PYEXE="
for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE echo [env] python : %PYEXE%
echo.
%PY% "%~dp0video_splitter.py" %*
set "CODE=%errorlevel%"
goto :done

:done
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

:no_runner
echo.
echo [ERROR] Neither a packaged executable nor Python 3 was found.
echo.
echo Two ways to fix this:
echo   1) Build the standalone executable once (no Python needed afterwards):
echo        python build_exe.py
echo      After that this file uses dist\video_splitter.exe automatically.
echo   2) Or install Python 3 from https://www.python.org/downloads/
echo      and make sure "Add python.exe to PATH" is checked during setup.
echo.
pause
exit /b 1
