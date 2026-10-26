$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv --without-pip .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建独立环境' }
}
& $taskPython -c "import fastapi,uvicorn,httpx,pypdf,pptx,docx,jieba,multipart"
if ($LASTEXITCODE -ne 0) {
    python -m pip --python .venv install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
}
& $taskPython -c "from hikari.core import Config; import sys; sys.exit(0 if Config().get('password') else 2)"
if ($LASTEXITCODE -eq 2) {
    & $taskPython -m hikari setup
    if ($LASTEXITCODE -ne 0) { throw '请完成账户设置' }
}
& $taskPython -m hikari
