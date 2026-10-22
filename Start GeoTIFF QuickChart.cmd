@echo off
setlocal
cd /d "%~dp0"

set "QUICKCHART_PYTHON_EXE="
if defined QUICKCHART_PYTHON call :try_python "%QUICKCHART_PYTHON%"
if not defined QUICKCHART_PYTHON_EXE if defined CONDA_PREFIX call :try_python "%CONDA_PREFIX%\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "D:\ProgramData\miniconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "%USERPROFILE%\miniconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE call :try_python "%USERPROFILE%\anaconda3\python.exe"
if not defined QUICKCHART_PYTHON_EXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined QUICKCHART_PYTHON_EXE call :try_python "%%P"

if not defined QUICKCHART_PYTHON_EXE (
    echo Could not find Python with a working Pillow installation.
    echo Set QUICKCHART_PYTHON to the full path of a compatible python.exe, or activate its Conda environment and try again.
    pause
    exit /b 1
)

"%QUICKCHART_PYTHON_EXE%" "%~dp0geotiff_qct_gui.py" %*
if errorlevel 1 (
    echo.
    echo GeoTIFF QuickChart did not start successfully. See the error above.
    pause
    exit /b 1
)

endlocal
exit /b 0

:try_python
if not exist "%~1" exit /b 1
"%~1" -c "from PIL import Image" >nul 2>nul
if errorlevel 1 exit /b 1
set "QUICKCHART_PYTHON_EXE=%~1"
exit /b 0
