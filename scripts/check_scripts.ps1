$ErrorActionPreference = 'Stop'
$taskFailures = @()
foreach ($taskFile in (Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1')) {
    $taskBytes = [System.IO.File]::ReadAllBytes($taskFile.FullName)
    $taskText = [System.Text.Encoding]::UTF8.GetString($taskBytes)
    $taskHasBom = $taskBytes.Length -ge 3 -and $taskBytes[0] -eq 239 -and $taskBytes[1] -eq 187 -and $taskBytes[2] -eq 191
    if ($taskText -match '[^\x00-\x7F]' -and -not $taskHasBom) {
        $taskFailures += "$($taskFile.Name): non-ASCII scripts require UTF-8 BOM for Windows PowerShell."
    }
    $taskTokens = $null
    $taskErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($taskFile.FullName, [ref]$taskTokens, [ref]$taskErrors) | Out-Null
    foreach ($taskError in $taskErrors) {
        $taskFailures += "$($taskFile.Name): $($taskError.Message)"
    }
}
if ($taskFailures.Count -gt 0) {
    $taskFailures | ForEach-Object { Write-Output $_ }
    exit 1
}
Write-Output "PowerShell $($PSVersionTable.PSVersion): script encoding and syntax OK."
