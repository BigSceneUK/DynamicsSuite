@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo Starting Dynamics Suite
echo ===================================================

set "PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"
set "UV_LINK_MODE=copy"

REM 1. Check if running directly inside a temporary zip folder
echo "%~dp0" | findstr /i "AppData\\Local\\Temp\\Temp" >nul
if %errorlevel% equ 0 goto :RUNNING_IN_ZIP

REM Check if uv is installed in system or user space
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [uv] 'uv' not found. Installing uv in user space...
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"
)

where uv >nul 2>nul
if %errorlevel% neq 0 goto :NO_UV

REM 2. Ensure virtual environment exists and satisfies Python >= 3.10
set "RECREATE_VENV=0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [venv] Existing virtual environment uses an older Python version.
        echo [venv] Recreating virtual environment with Python 3.11...
        set "RECREATE_VENV=1"
    )
) else (
    set "RECREATE_VENV=1"
)

if "%RECREATE_VENV%"=="1" (
    echo [venv] Preparing virtual environment with Python 3.11 using uv...
    echo [venv] (If Python 3.11 is not on this machine, uv will download a portable runtime automatically)
    if exist ".venv" (
        attrib -r -s -h -u -p ".venv\*" /s /d >nul 2>nul
        rd /s /q ".venv" >nul 2>nul
    )
    uv venv --python 3.11 .venv
    if errorlevel 1 goto :VENV_FAILED
)

REM 3. Check if dependencies need installation
set "DO_INSTALL=0"
if "%RECREATE_VENV%"=="1" set "DO_INSTALL=1"
if not exist ".venv\Scripts\streamlit.exe" set "DO_INSTALL=1"
if /i "%~1"=="--update" set "DO_INSTALL=1"
if /i "%~1"=="-u" set "DO_INSTALL=1"

if "%DO_INSTALL%"=="1" (
    echo [deps] Installing dependencies with uv...
    uv pip install pip -r requirements.txt
    if errorlevel 1 goto :DEPS_FAILED

    if exist "install_plugins.py" (
        if exist ".venv\Scripts\python.exe" (
            ".venv\Scripts\python.exe" install_plugins.py
        )
    )
) else (
    echo [deps] Virtual environment ready. Launching immediately...
)

REM 5. Launch Streamlit app
echo [launch] Launching Dynamics Suite...
if exist ".venv\Scripts\streamlit.exe" (
    ".venv\Scripts\streamlit.exe" run app.py
) else if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m streamlit run app.py
) else (
    goto :NO_STREAMLIT
)

pause
exit /b 0

:RUNNING_IN_ZIP
echo.
echo [ERROR] You are running start.bat directly from inside the ZIP file!
echo Please EXTRACT the zip first: Right-click DynamicsSuite-latest.zip and select "Extract All...".
pause
exit /b 1

:NO_UV
echo.
echo [ERROR] Could not find or install 'uv'.
echo Please install uv manually from https://astral.sh/uv and run start.bat again.
pause
exit /b 1

:VENV_FAILED
echo.
echo =====================================================================
echo [ERROR] Failed to create virtual environment (Error 4350 / Offline file).
echo This occurs when files are synced to OneDrive or run from inside a ZIP:
echo   1. If inside OneDrive (Desktop/Documents), MOVE DynamicsSuite to
echo      a local folder like C:\DynamicsSuite, or right-click this folder
echo      and select "Always keep on this device".
echo   2. Delete any existing .venv folder and try again.
echo   3. Make sure you Extracted the ZIP (do not run inside the zip).
echo =====================================================================
pause
exit /b 1

:DEPS_FAILED
echo.
echo [ERROR] Failed to install dependencies.
pause
exit /b 1

:NO_STREAMLIT
echo.
echo [ERROR] Streamlit executable not found in .venv\Scripts.
pause
exit /b 1
