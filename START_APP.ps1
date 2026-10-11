$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host ""
Write-Host "=== Displate One-Click Preparer ===" -ForegroundColor Cyan
Write-Host ""

function Find-Python {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    )
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { return $candidate } }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

$python = Find-Python
if (-not $python -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Installing Python 3.11..." -ForegroundColor Yellow
    winget install --id Python.Python.3.11 --exact --accept-source-agreements --accept-package-agreements
    $python = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
}
if (-not $python -or -not (Test-Path $python)) {
    Write-Host "Python 3.11+ is required." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-Path ".venv\Scripts\python.exe")) { & $python -m venv .venv }
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$hash = (Get-FileHash "requirements.txt" -Algorithm SHA256).Hash
$stamp = Join-Path $PSScriptRoot ".venv\requirements.hash"
if (-not (Test-Path $stamp) -or ((Get-Content $stamp -Raw).Trim() -ne $hash)) {
    Write-Host "Installing packages..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip setuptools wheel
    & $venvPython -m pip install -r requirements.txt
    Set-Content $stamp $hash
}

$port = 7972
if (Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue) {
    Start-Process "http://127.0.0.1:$port"
    exit 0
}
Start-Process powershell -ArgumentList "-NoProfile -WindowStyle Hidden -Command Start-Sleep 2; Start-Process 'http://127.0.0.1:$port'"
Write-Host "Opening http://127.0.0.1:$port" -ForegroundColor Green
& $venvPython -m uvicorn app:app --host 127.0.0.1 --port $port
