@echo off
REM ===================================================================
REM  Build EasySMB into a single .exe using PyInstaller.
REM  Double-click this file, or run it from a command prompt.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo  EasySMB build
echo  =============
echo.

REM --- 1. Python present? --------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed, or not on your PATH.
    echo         Install it from python.org and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo  Using %%v

REM --- 2. PyInstaller present? ---------------------------------------
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo  PyInstaller not found - installing it now...
    python -m pip install --upgrade pyinstaller
    if errorlevel 1 (
        echo.
        echo  ERROR: could not install PyInstaller. Check your internet connection.
        echo.
        pause
        exit /b 1
    )
)
for /f "delims=" %%v in ('python -m PyInstaller --version 2^>^&1') do echo  Using PyInstaller %%v

REM --- 3. Required files ---------------------------------------------
if not exist "smb_manager_GUI.pyw" (
    echo  ERROR: smb_manager_GUI.pyw is missing from %cd%
    echo.
    pause
    exit /b 1
)
if not exist "smb_manager_GUI.spec" (
    echo  ERROR: smb_manager_GUI.spec is missing from %cd%
    echo         That file is what marks the exe as "run as administrator".
    echo.
    pause
    exit /b 1
)
if not exist "app_icon.ico" (
    echo  WARNING: app_icon.ico is missing - the build may fail on the icon.
)

REM --- 4. Syntax check before the slow part ---------------------------
echo.
echo  Checking the script parses...
python -c "import ast,io,sys; ast.parse(io.open('smb_manager_GUI.pyw',encoding='utf-8').read())"
if errorlevel 1 (
    echo.
    echo  ERROR: smb_manager_GUI.pyw has a syntax error - see above.
    echo         Nothing was built, and your previous dist\smb_manager_GUI.exe
    echo         has been left exactly as it was.
    echo.
    pause
    exit /b 1
)
echo  OK.

REM --- 4b. Catch bugs a syntax check cannot see -----------------------
REM  A literal %% inside a string that is then %%-formatted parses fine and
REM  only explodes when the button is pressed. This is what broke "Turn on
REM  phone access" in 2.0.0.
if exist "tools\check_format_strings.py" (
    echo  Checking format strings...
    python tools\check_format_strings.py
    if errorlevel 1 (
        echo.
        echo  ERROR: a format string would fail at runtime - see above.
        echo         Nothing was built, and your previous exe is untouched.
        echo.
        pause
        exit /b 1
    )
)

REM --- 5. Delete the previous build -----------------------------------
REM  This happens BEFORE building on purpose. If a build fails, there must
REM  be no old exe left sitting in dist\ that you could mistake for the new
REM  one - PyInstaller leaves the previous file exactly where it was.
echo.
echo  Deleting the previous build...
if exist "build" rmdir /s /q "build" 2>nul
if exist "dist"  rmdir /s /q "dist"  2>nul

if exist "dist\smb_manager_GUI.exe" (
    echo.
    echo  ERROR: the previous smb_manager_GUI.exe could not be deleted.
    echo         It is almost certainly still running.
    echo.
    echo         Close EasySMB - check the taskbar and the system tray -
    echo         then run this again.
    echo.
    pause
    exit /b 1
)
if exist "build" (
    echo.
    echo  ERROR: the build folder could not be cleared. Something has a file
    echo         in %cd%\build open. Close it, or delete that folder yourself.
    echo.
    pause
    exit /b 1
)
echo  OK.

REM --- 6. Build -------------------------------------------------------
echo  Building (this takes a minute)...
echo.
python -m PyInstaller --noconfirm --clean smb_manager_GUI.spec
if errorlevel 1 (
    echo.
    echo  ===============================================================
    echo   BUILD FAILED - the PyInstaller output above says why.
    echo  ===============================================================
    echo.
    pause
    exit /b 1
)

if not exist "dist\smb_manager_GUI.exe" (
    echo.
    echo  ERROR: PyInstaller reported success but dist\smb_manager_GUI.exe
    echo         is not there. Check the output above.
    echo.
    pause
    exit /b 1
)

REM --- 7. Done --------------------------------------------------------
echo.
echo  ===============================================================
echo   BUILD OK
echo  ===============================================================
echo.
echo   Your program:  %cd%\dist\smb_manager_GUI.exe
echo.
echo   The .spec sets uac_admin=True, so Windows shows the
echo   administrator prompt every time the exe is started. That is
echo   what EasySMB needs to change permissions and shares.
echo.
echo   Copy easynas_config.json next to the exe if you are moving an
echo   existing setup - that is where the server folder, hidden
echo   accounts and share layout are stored.
echo.
pause
exit /b 0
