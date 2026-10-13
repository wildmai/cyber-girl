$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
Write-Host '赛博女友 · 本地服务 http://127.0.0.1:7860'
Write-Host '关闭此窗口或按 Ctrl+C 结束。模型权重不会自动下载。'
& $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 7860 --ws-max-size 16384
