@echo off
REM run_signature.bat - Interactive wrapper for add_signature_to_pdf
REM Automatically activates venv and runs with default/interactive parameters

setlocal enabledelayedexpansion

REM Get script directory
set "SCRIPT_DIR=%~dp0"

REM Default values
set "INPUT_DIR=.\input"
set "OUTPUT_DIR=.\output"
set "NAME=hulyatun maskunah"
set "SIGNATURE=.\hulya_signature.jpeg"

REM Check for -Interactive flag or no arguments
set "INTERACTIVE=0"
if "%~1"=="" set "INTERACTIVE=1"
if "%~1"=="-Interactive" set "INTERACTIVE=1"

if "%INTERACTIVE%"=="1" (
    echo === Signature PDF Tool ===
    echo.

    set /p "INPUT_DIR=Input folder path [%INPUT_DIR%]: "
    if "!INPUT_DIR!"=="" set "INPUT_DIR=.\input"

    set /p "OUTPUT_DIR=Output folder path [%OUTPUT_DIR%]: "
    if "!OUTPUT_DIR!"=="" set "OUTPUT_DIR=.\output"

    set /p "NAME=Signature name [%NAME%]: "
    if "!NAME!"=="" set "NAME=hulyatun maskunah"

    set /p "SIGNATURE=Signature image path [%SIGNATURE%]: "
    if "!SIGNATURE!"=="" set "SIGNATURE=.\hulya_signature.jpeg"
)

echo.
echo Running with parameters:
echo   Input:      %INPUT_DIR%
echo   Output:     %OUTPUT_DIR%
echo   Name:       %NAME%
echo   Signature:  %SIGNATURE%
echo.

REM Use python directly from venv
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

REM Run the Python script
"%PYTHON_EXE%" "%SCRIPT_DIR%main.py" --input "%INPUT_DIR%" --output "%OUTPUT_DIR%" --name "%NAME%" --signature "%SIGNATURE%"

endlocal