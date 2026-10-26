$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& powershell.exe -NoProfile -NonInteractive -File scripts/check_scripts.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pytest -q
exit $LASTEXITCODE
