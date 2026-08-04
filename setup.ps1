# canval - Windows setup
# Run from the folder this file sits in:
#     powershell -ExecutionPolicy Bypass -File .\setup.ps1

Write-Host "`n=== canval setup ===" -ForegroundColor Cyan

# 1. Python present?
try {
    $v = python --version 2>&1
    Write-Host "  Python : $v" -ForegroundColor Green
} catch {
    Write-Host "  Python not found. Install it from python.org first." -ForegroundColor Red
    exit 1
}

# 2. Dependency
Write-Host "`n  Installing requests ..." -ForegroundColor Cyan
python -m pip install --quiet --upgrade requests
if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip failed. Try:  python -m pip install requests" -ForegroundColor Red
    exit 1
}
Write-Host "  requests ready" -ForegroundColor Green

# 3. Package layout sanity check
$missing = @()
foreach ($f in @("__init__.py","config.py","xdm.py","parsers.py","store.py",
                 "index.py","monitor.py","afaqy.py","scan.py","cli.py")) {
    if (-not (Test-Path ".\canval\$f")) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Write-Host "`n  Missing from .\canval\ :" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "     $_" -ForegroundColor Red }
    Write-Host "  Extract the archive again, keeping its folder structure." -ForegroundColor Yellow
    exit 1
}
Write-Host "  All 10 modules present" -ForegroundColor Green

# 4. Does it run?
Write-Host "`n  Testing ..." -ForegroundColor Cyan
python -m canval.cli --help *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Import failed. Run this to see why:" -ForegroundColor Red
    Write-Host "     python -m canval.cli --help" -ForegroundColor Yellow
    exit 1
}

Write-Host "  canval runs" -ForegroundColor Green
Write-Host "`n=== ready ===" -ForegroundColor Cyan
Write-Host @"

Next, set your credentials in THIS window (they are not saved to disk):

    `$env:XDM_CLIENT_ID='...'
    `$env:XDM_CLIENT_SECRET='...'
    `$env:XDM_REGION='eu'

Then pull the catalogue:

    python -m canval.cli catalogue

"@ -ForegroundColor White
