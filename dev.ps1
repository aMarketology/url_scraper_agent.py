# ObjectWire Development Mode
# Watches for file changes and auto-reinstalls the CLI

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcPath = Join-Path $ScriptDir "src"
$LastHash = ""

Write-Host ""
Write-Host "========================================" -ForegroundColor DarkYellow
Write-Host "  ObjectWire Dev Mode (Auto-Reload)" -ForegroundColor DarkYellow  
Write-Host "========================================" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "Watching for changes in: $SrcPath" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

function Get-FolderHash {
    $files = Get-ChildItem -Path $SrcPath -Recurse -Include "*.py" -ErrorAction SilentlyContinue
    $hashes = $files | ForEach-Object { 
        (Get-FileHash $_.FullName -Algorithm MD5).Hash 
    }
    return ($hashes -join "")
}

function Install-Package {
    Write-Host ""
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Change detected! Reinstalling..." -ForegroundColor Yellow
    
    # Kill any running objectwire process
    taskkill /IM objectwire.exe /F 2>$null | Out-Null
    Start-Sleep -Milliseconds 500
    
    # Reinstall
    $output = & "$ScriptDir\.venv\Scripts\pip.exe" install -e $ScriptDir 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] SUCCESS - Package reinstalled!" -ForegroundColor Green
        Write-Host ""
    } else {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ERROR - Installation failed!" -ForegroundColor Red
        Write-Host "----------------------------------------" -ForegroundColor Red
        $output | ForEach-Object { Write-Host $_ -ForegroundColor Red }
        Write-Host "----------------------------------------" -ForegroundColor Red
        Write-Host ""
    }
}

# Initial install
Install-Package
$LastHash = Get-FolderHash

# Watch loop
while ($true) {
    Start-Sleep -Seconds 2
    
    $CurrentHash = Get-FolderHash
    
    if ($CurrentHash -ne $LastHash) {
        Install-Package
        $LastHash = $CurrentHash
    }
}
