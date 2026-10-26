$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskPort = & .\.venv\Scripts\python.exe scripts/check_service.py
if ($LASTEXITCODE -ne 0) { throw '请先启动正式助手服务，再建立外网连接。' }
$taskTunnel = Join-Path (Get-Location) 'tools\cloudflared.exe'
if (-not (Test-Path -LiteralPath $taskTunnel)) {
    & .\.venv\Scripts\python.exe scripts/install_tunnel.py
    if ($LASTEXITCODE -ne 0) { throw '连接工具下载失败，请检查网络或使用官方安装方式。' }
}
Write-Host "已确认 Hikari 正式服务位于本机端口 $taskPort。临时网址会随连接重建变化。"
& $taskTunnel tunnel --protocol http2 --url "http://127.0.0.1:$taskPort"
