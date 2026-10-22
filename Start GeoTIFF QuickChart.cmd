@echo off
setlocal
cd /d "%~dp0"

set "QUICKCHART_PYTHON_EXE="
set "QUICKCHART_NEEDS_SETUP="
set "QUICKCHART_GDAL_MISSING="
set "QUICKCHART_MAMBA=%LOCALAPPDATA%\GeoTIFFQuickChart\micromamba.exe"
set "QUICKCHART_ENV=%LOCALAPPDATA%\GeoTIFFQuickChart\environment"

if exist "%QUICKCHART_MAMBA%" if exist "%QUICKCHART_ENV%\python.exe" (
    "%QUICKCHART_MAMBA%" run -p "%QUICKCHART_ENV%" python -c "from PIL import Image; import os" >nul 2>nul
    if not errorlevel 1 goto :run_managed
)

if defined QUICKCHART_PYTHON call :try_python "%QUICKCHART_PYTHON%"
if not defined QUICKCHART_PYTHON_EXE if defined CONDA_PREFIX call :try_python "%CONDA_PREFIX%\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "D:\ProgramData\miniconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "%USERPROFILE%\miniconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "%USERPROFILE%\anaconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined QUICKCHART_PYTHON_EXE call :try_python "%%P"

set "QUICKCHART_GDAL_MISSING="
where gdalinfo >nul 2>nul || set "QUICKCHART_GDAL_MISSING=1"
where gdal_translate >nul 2>nul || set "QUICKCHART_GDAL_MISSING=1"
where gdaltransform >nul 2>nul || set "QUICKCHART_GDAL_MISSING=1"

if not defined QUICKCHART_PYTHON_EXE (
    echo Python with Pillow is missing or not working.
    set "QUICKCHART_NEEDS_SETUP=1"
)
if defined QUICKCHART_GDAL_MISSING (
    echo One or more required GDAL command-line tools are missing from PATH.
    set "QUICKCHART_NEEDS_SETUP=1"
)

if defined QUICKCHART_NEEDS_SETUP (
    echo.
    echo You can install an isolated Python and GDAL environment for this app.
    echo This downloads packages from conda-forge into your Windows user profile.
    echo It does not install or change Docker or Podman.
    choice /c YN /n /m "Install or repair the app environment now? [Y/N] "
    if errorlevel 2 goto :use_existing
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup GeoTIFF QuickChart.ps1"
    if errorlevel 1 (
        echo.
        echo Setup did not complete. See the message above.
        pause
        exit /b 1
    )
    if exist "%QUICKCHART_MAMBA%" goto :run_managed
    echo Setup completed but the isolated environment could not be found.
    pause
    exit /b 1
)

:use_existing
if not defined QUICKCHART_PYTHON_EXE (
    echo No usable Python/Pillow environment was found. Run this file again and choose setup.
    pause
    exit /b 1
)
if defined QUICKCHART_GDAL_MISSING (
    echo WARNING: the GUI will open, but conversion is unavailable until GDAL is installed or selected in the GUI.
)
"%QUICKCHART_PYTHON_EXE%" "%~dp0geotiff_qct_gui.py" %*
if errorlevel 1 goto :failed
endlocal
exit /b 0

:run_managed
"%QUICKCHART_MAMBA%" run -p "%QUICKCHART_ENV%" python "%~dp0geotiff_qct_gui.py" %*
if errorlevel 1 goto :failed
endlocal
exit /b 0

:failed
echo.
echo GeoTIFF QuickChart did not start successfully. See the error above.
pause
exit /b 1

:try_python
if not exist "%~1" exit /b 1
"%~1" -c "import sys; from PIL import Image; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 exit /b 1
set "QUICKCHART_PYTHON_EXE=%~1"
exit /b 0
