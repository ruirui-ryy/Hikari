$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskPort = & .\.venv\Scripts\python.exe scripts/check_service.py
if ($LASTEXITCODE -ne 0) { throw '请先运行 scripts/start.ps1，并保持助手运行。' }
$taskCommand = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if ($taskCommand) {
    $taskTailscale = $taskCommand.Source
} else {
    $taskTailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
}
if (-not (Test-Path -LiteralPath $taskTailscale)) { throw '请先安装并登录 Tailscale。' }
$taskStateText = & .\.venv\Scripts\python.exe scripts/tailscale_json.py $taskTailscale status
if ($LASTEXITCODE -ne 0) { throw '无法连接 Tailscale，请确认已启动并登录。' }
$taskState = $taskStateText | ConvertFrom-Json
if ($taskState.BackendState -ne 'Running') { throw '请先连接 Tailscale。' }
$taskExistingText = & .\.venv\Scripts\python.exe scripts/tailscale_json.py $taskTailscale serve
if ($LASTEXITCODE -ne 0) { throw '无法检查已有 Serve 配置，未进行修改。' }
$taskExisting = $taskExistingText | ConvertFrom-Json
$taskTarget = "http://127.0.0.1:$taskPort"
if ($taskExisting.TCP.'443' -and -not $taskExisting.TCP.'443'.HTTPS) {
    throw 'Tailscale 443 端口已有其他转发，未覆盖。'
}
foreach ($taskWeb in $taskExisting.Web.PSObject.Properties) {
    if ($taskWeb.Name.EndsWith(':443')) {
        foreach ($taskHandler in $taskWeb.Value.Handlers.PSObject.Properties) {
            if ($taskHandler.Name -ne '/' -or $taskHandler.Value.Proxy -ne $taskTarget) {
                throw 'Tailscale HTTPS 入口已有其他服务，未覆盖。'
            }
        }
    }
}
foreach ($taskFunnel in $taskExisting.AllowFunnel.PSObject.Properties) {
    if ($taskFunnel.Name.EndsWith(':443') -and $taskFunnel.Value) {
        throw '443 入口已启用公网 Funnel，未自动更改，请先检查现有用途。'
    }
}
& $taskTailscale serve --bg --https=443 $taskTarget
if ($LASTEXITCODE -ne 0) { throw 'Serve 配置失败，请查看上方提示。' }
$taskName = $taskState.Self.DNSName.TrimEnd('.')
Write-Host "手机连接 Tailscale 后打开：https://$taskName"
Write-Host '配置由 Tailscale 持续保存。以后只需启动 Hikari 并保持两端 Tailscale 在线。'
