# ObjectWire Global Installation Script
# Run this script to install ObjectWire CLI globally on Windows

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "ObjectWire CLI Installer" -ForegroundColor DarkYellow
Write-Host "========================" -ForegroundColor DarkYellow
Write-Host ""

# Get the directory where this script is located
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvScripts = Join-Path $ScriptDir ".venv\Scripts"
$ObjectWireExe = Join-Path $VenvScripts "objectwire.exe"

# Check if .venv exists
if (-not (Test-Path $VenvScripts)) {
    Write-Host "[X] Virtual environment not found. Creating..." -ForegroundColor Red
    & python -m venv (Join-Path $ScriptDir ".venv")
    Write-Host "[OK] Virtual environment created" -ForegroundColor Green
}

# Install the package
Write-Host "[*] Installing ObjectWire package..." -ForegroundColor Cyan
& (Join-Path $VenvScripts "pip.exe") install -e $ScriptDir --quiet
Write-Host "[OK] Package installed" -ForegroundColor Green

# Create a wrapper batch file in a global location
$GlobalBinDir = "$env:USERPROFILE\.objectwire\bin"
if (-not (Test-Path $GlobalBinDir)) {
    New-Item -ItemType Directory -Path $GlobalBinDir -Force | Out-Null
}

# Create objectwire.cmd wrapper
$WrapperPath = Join-Path $GlobalBinDir "objectwire.cmd"
$WrapperContent = "@echo off"
$WrapperContent += "`r`n"
$WrapperContent += "`"$ObjectWireExe`" %*"
Set-Content -Path $WrapperPath -Value $WrapperContent -Encoding ASCII

Write-Host "[OK] Created global wrapper at $WrapperPath" -ForegroundColor Green

# Check if already in PATH
$CurrentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($CurrentPath -notlike "*$GlobalBinDir*") {
    # Add to user PATH
    $NewPath = $GlobalBinDir + ";" + $CurrentPath
    [Environment]::SetEnvironmentVariable("PATH", $NewPath, "User")
    Write-Host "[OK] Added to user PATH" -ForegroundColor Green
    Write-Host ""
    Write-Host "[!] Please restart your terminal for PATH changes to take effect!" -ForegroundColor Yellow
} else {
    Write-Host "[OK] Already in PATH" -ForegroundColor Green
}

Write-Host ""
Write-Host "================================================" -ForegroundColor DarkYellow
Write-Host "[OK] Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Usage:" -ForegroundColor Cyan
Write-Host "  objectwire          Launch interactive CLI" -ForegroundColor White
Write-Host "  objectwire --help   Show all commands" -ForegroundColor White
Write-Host ""
