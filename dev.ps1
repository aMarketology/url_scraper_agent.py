# ObjectWire Development Mode
# Simple wrapper that uses the built-in --dev flag

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "Starting ObjectWire in dev mode..." -ForegroundColor Cyan
Write-Host ""

# Use the built-in --dev flag
& "$ScriptDir\.venv\Scripts\python.exe" -m objectwire --dev
