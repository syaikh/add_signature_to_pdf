# run_signature.ps1 - Interactive wrapper for add_signature_to_pdf
# Automatically activates venv and runs with default/interactive parameters

param(
    [string]$InputDir = "",
    [string]$OutputDir = "",
    [string]$Name = "",
    [string]$Signature = ""
)

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Default values
$DefaultInput = ".\input"
$DefaultOutput = ".\output"
$DefaultName = "hulyatun maskunah"
$DefaultSignature = ".\hulya_signature.jpeg"

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
$Interactive = $false
if ($args.Count -eq 0 -or $args[0] -eq "-Interactive") {
    $Interactive = $true
}

if ($Interactive) {
    Write-Host "=== Signature PDF Tool ===" -ForegroundColor Cyan
    Write-Host ""

    $InputDir = Read-WithDefault -Prompt "Input folder path" -DefaultValue $DefaultInput
    $OutputDir = Read-WithDefault -Prompt "Output folder path" -DefaultValue $DefaultOutput
    $Name = Read-WithDefault -Prompt "Signature name" -DefaultValue $DefaultName
    $Signature = Read-WithDefault -Prompt "Signature image path" -DefaultValue $DefaultSignature
} else {
    # Use provided values or defaults
    if ([string]::IsNullOrWhiteSpace($InputDir)) { $InputDir = $DefaultInput }
    if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = $DefaultOutput }
    if ([string]::IsNullOrWhiteSpace($Name)) { $Name = $DefaultName }
    if ([string]::IsNullOrWhiteSpace($Signature)) { $Signature = $DefaultSignature }
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