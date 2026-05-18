# run_signature.ps1 - Interactive wrapper for add_signature_to_pdf
# Automatically activates venv and runs with default/interactive parameters

param(
    [string]$InputDir = "./input",
    [string]$OutputDir = "./output",
    [string]$Name = "hulyatun maskunah",
    [string]$Signature = "./hulya_signature.jpeg"
)

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Use python directly from venv to avoid activation scope issues
$PythonExe = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Warning "Python not found in venv at $PythonExe"
    $PythonExe = "python"
}

# Function to prompt with default value
function Read-WithDefault {
    param(
        [string]$Prompt,
        [string]$DefaultValue
    )
    $userInput = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($userInput)) {
        return $DefaultValue
    }
    return $userInput
}

# Interactive mode if no arguments provided
if ($args.Count -eq 0 -or $args[0] -eq "-Interactive") {
    Write-Host "=== Signature PDF Tool ===" -ForegroundColor Cyan
    Write-Host ""

    $InputDir = Read-WithDefault -Prompt "Input folder path" -DefaultValue $InputDir
    $OutputDir = Read-WithDefault -Prompt "Output folder path" -DefaultValue $OutputDir
    $Name = Read-WithDefault -Prompt "Signature name" -DefaultValue $Name
    $Signature = Read-WithDefault -Prompt "Signature image path" -DefaultValue $Signature
}

$mainScript = Join-Path $ScriptDir "main.py"

Write-Host ""
Write-Host "Running with parameters:" -ForegroundColor Yellow
Write-Host "  Input:      $InputDir"
Write-Host "  Output:     $OutputDir"
Write-Host "  Name:       $Name"
Write-Host "  Signature:  $Signature"
Write-Host ""

& $PythonExe $mainScript --input $InputDir --output $OutputDir --name "$Name" --signature $Signature