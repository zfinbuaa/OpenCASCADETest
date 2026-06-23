@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================================
echo   AutoModel Portable Build Script
echo ============================================================
echo.
echo [INFO] Working directory: %CD%
echo.

REM --- Read version ---
if not exist VERSION.txt (
    echo [ERROR] VERSION.txt not found
    pause
    exit /b 1
)
set /p VERSION=<VERSION.txt
echo [INFO] Version: !VERSION!

REM --- Verify conda environment ---
set "PYTHON="
if exist "%USERPROFILE%\miniconda3\envs\pyoccenv\python.exe" (
    set "PYTHON=%USERPROFILE%\miniconda3\envs\pyoccenv\python.exe"
) else if exist "%USERPROFILE%\Anaconda3\envs\pyoccenv\python.exe" (
    set "PYTHON=%USERPROFILE%\Anaconda3\envs\pyoccenv\python.exe"
) else if exist "%USERPROFILE%\AppData\Local\miniconda3\envs\pyoccenv\python.exe" (
    set "PYTHON=%USERPROFILE%\AppData\Local\miniconda3\envs\pyoccenv\python.exe"
)
if "!PYTHON!"=="" (
    echo [ERROR] Conda environment 'pyoccenv' not found
    echo   Checked:
    echo     %USERPROFILE%\miniconda3\envs\pyoccenv\python.exe
    echo     %USERPROFILE%\Anaconda3\envs\pyoccenv\python.exe
    echo     %USERPROFILE%\AppData\Local\miniconda3\envs\pyoccenv\python.exe
    pause
    exit /b 1
)
echo [INFO] Using Python: !PYTHON!

REM --- Generate version_info.txt for PyInstaller ---
echo [INFO] Generating version_info.txt ...
"!PYTHON!" _gen_version.py
if errorlevel 1 (
    echo [ERROR] Failed to generate version_info.txt
    pause
    exit /b 1
)

REM --- Generate icon if missing ---
if not exist app_icon.ico (
    echo [INFO] Generating app_icon.ico ...
    "!PYTHON!" make_icon.py
    if errorlevel 1 (
        echo [WARN] Icon generation failed, continuing without icon
    )
)

REM --- Clean old build artifacts ---
echo.
echo [INFO] Cleaning old build artifacts ...
if exist build ( rmdir /s /q build && echo   Removed build\ )
if exist dist ( rmdir /s /q dist && echo   Removed dist\ )
if exist release ( rmdir /s /q release && echo   Removed release\ )
echo [INFO] Clean done.

REM --- Step 1: PyInstaller pipeline -> dist/AutoModel/ ---
echo.
echo ============================================================
echo   Step 1/3: Building Pipeline Engine (PyInstaller)
echo ============================================================
echo [CMD] "!PYTHON!" -m PyInstaller pipeline.spec
"!PYTHON!" -m PyInstaller pipeline.spec
set PYINSTALLER_ERR=!errorlevel!
if !PYINSTALLER_ERR! neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed with exit code !PYINSTALLER_ERR!
    echo   Check output above for details.
    pause
    exit /b 1
)
if not exist "dist\AutoModel\AutoModel.exe" (
    echo [ERROR] dist\AutoModel\AutoModel.exe not found after build
    pause
    exit /b 1
)
echo [INFO] Pipeline engine built successfully.

REM --- Step 2: Install NPM dependencies ---
echo.
echo ============================================================
echo   Step 2/3: Installing NPM dependencies
echo ============================================================
if not exist "node_modules\three" (
    echo [CMD] npm install
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install failed
        pause
        exit /b 1
    )
) else (
    echo [INFO] node_modules already exists, skipping npm install.
)

REM --- Step 3: Electron builder -> release/win-unpacked/ ---
echo.
echo ============================================================
echo   Step 3/3: Packaging Electron App (electron-builder --dir)
echo ============================================================
echo [CMD] npx electron-builder --dir
call npx electron-builder --dir
set BUILDER_ERR=!errorlevel!
if !BUILDER_ERR! neq 0 (
    echo.
    echo [ERROR] Electron-builder failed with exit code !BUILDER_ERR!
    echo   Check output above for details.
    pause
    exit /b 1
)

REM --- Verify output ---
set "UNPACKED=release\win-unpacked"
if not exist "%UNPACKED%\AutoModel.exe" (
    echo [ERROR] %UNPACKED%\AutoModel.exe not found
    echo   Electron-builder may have succeeded but the output
    echo   directory name or structure is unexpected.
    pause
    exit /b 1
)
if not exist "%UNPACKED%\resources\pipeline\AutoModel.exe" (
    echo [ERROR] Pipeline exe not bundled in resources
    echo   Check that dist\AutoModel exists before packaging.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Build completed successfully
echo   Output folder: %UNPACKED%
echo ============================================================

REM --- Create ZIP archive ---
set "ZIPNAME=AutoModel_!VERSION!_portable"
echo.
echo [INFO] Creating !ZIPNAME!.zip ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%UNPACKED%\*' -DestinationPath 'release\!ZIPNAME!.zip' -Force"
if errorlevel 1 (
    echo [WARN] ZIP creation failed, but unpacked folder is ready.
) else (
    echo [INFO] ZIP archive: release\!ZIPNAME!.zip
)

echo.
echo ============================================================
echo   ALL DONE
echo   Portable folder : %UNPACKED%
echo   ZIP archive     : release\!ZIPNAME!.zip
echo ============================================================
echo.
echo   To deploy, copy the entire '%UNPACKED%' folder to the
echo   target machine and double-click AutoModel.exe to start.
echo.
pause
