@echo off
REM run_signature.bat - Interactive wrapper for add_signature_to_pdf
REM Automatically activates venv and runs with default/interactive parameters

setlocal enabledelayedexpansion

REM Get script directory
set "SCRIPT_DIR=%~dp0"

REM Default values
set "DEFAULT_INPUT=.\input"
set "DEFAULT_OUTPUT=.\output"
set "DEFAULT_NAME=hulyatun maskunah"
set "DEFAULT_SIGNATURE=.\hulya_signature.jpeg"

REM Set values from arguments or use defaults
if "%~1"=="" (set "INPUT_DIR=") else (set "INPUT_DIR=%~1")
if "%~2"=="" (set "OUTPUT_DIR=") else (set "OUTPUT_DIR=%~2")
if "%~3"=="" (set "NAME=") else (set "NAME=%~3")
if "%~4"=="" (set "SIGNATURE=") else (set "SIGNATURE=%~4")

REM Check for -Interactive flag or no arguments
set "INTERACTIVE=0"
if "%~1"=="" set "INTERACTIVE=1"
if "%~1"=="-Interactive" set "INTERACTIVE=1"

if "%INTERACTIVE%"=="1" (
    echo === Signature PDF Tool ===
    echo.

    set /p "INPUT_DIR=Input folder path [%DEFAULT_INPUT%]: "
    if "!INPUT_DIR!"=="" set "INPUT_DIR=%DEFAULT_INPUT%"

    set /p "OUTPUT_DIR=Output folder path [%DEFAULT_OUTPUT%]: "
    if "!OUTPUT_DIR!"=="" set "OUTPUT_DIR=%DEFAULT_OUTPUT%"

    set /p "NAME=Signature name [%DEFAULT_NAME%]: "
    if "!NAME!"=="" set "NAME=%DEFAULT_NAME%"

    set /p "SIGNATURE=Signature image path [%DEFAULT_SIGNATURE%]: "
    if "!SIGNATURE!"=="" set "SIGNATURE=%DEFAULT_SIGNATURE%"
) else (
    REM Apply defaults for empty values
    if "%INPUT_DIR%"=="" set "INPUT_DIR=%DEFAULT_INPUT%"
    if "%OUTPUT_DIR%"=="" set "OUTPUT_DIR=%DEFAULT_OUTPUT%"
    if "%NAME%"=="" set "NAME=%DEFAULT_NAME%"
    if "%SIGNATURE%"=="" set "SIGNATURE=%DEFAULT_SIGNATURE%"
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