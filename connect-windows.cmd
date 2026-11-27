@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VC_CONNECT_ENV=%LOCALAPPDATA%\TelegramVcAutopost\venv"

echo Telegram to vc.ru - one-time connection
where py.exe >nul 2>&1
if not errorlevel 1 py -3 -c "import sys; sys.exit((3, 10) > sys.version_info[:2])" >nul 2>&1
if errorlevel 1 (
    echo Installing Python from Windows Package Manager...
    winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto failed
)

where gh.exe >nul 2>&1
if errorlevel 1 (
    echo Installing GitHub CLI from Windows Package Manager...
    winget install --exact --id GitHub.cli --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto failed
    set "PATH=%ProgramFiles%\GitHub CLI;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
)
where gh.exe >nul 2>&1
if errorlevel 1 goto reopen
if not exist "%VC_CONNECT_ENV%\Scripts\python.exe" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv "%VC_CONNECT_ENV%"
    ) else (
        py -3 -m venv "%VC_CONNECT_ENV%"
    )
    if errorlevel 1 goto failed
)
"%VC_CONNECT_ENV%\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto failed

gh auth status --hostname github.com >nul 2>&1
if errorlevel 1 (
    echo Sign in to GitHub as proavitology-droid when prompted.
    gh auth login --hostname github.com --web --git-protocol https
    if errorlevel 1 goto failed
)
"%VC_CONNECT_ENV%\Scripts\python.exe" connect.py
if errorlevel 1 goto failed
pause
exit /b 0

:reopen
echo GitHub CLI was installed. Close this window and run connect-windows.cmd again.
pause
exit /b 1

:failed
echo Connection did not finish. Check the message above.
pause
exit /b 1
