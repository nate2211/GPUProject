$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    & $Python -3.12 -m venv .venv
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    & $Python -m venv .venv
} else {
    throw "Python 3.11 or 3.12 was not found."
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip wheel
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host ""
Write-Host "Setup complete."
Write-Host "Start with: .\.venv\Scripts\python.exe main.py"
