$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskExe = Join-Path $taskRoot 'output\desktop\Xiaoman-win32-x64\Xiaoman.exe'
if (-not (Test-Path -LiteralPath $taskExe)) {
    throw '未找到桌面程序，请先在 desktop 目录执行 npm ci 和 npm run build。'
}
$taskDesktop = [Environment]::GetFolderPath('Desktop')
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut((Join-Path $taskDesktop '小满.lnk'))
$taskShortcut.TargetPath = $taskExe
$taskShortcut.WorkingDirectory = $taskRoot
$taskShortcut.IconLocation = "$taskExe,0"
$taskShortcut.Description = '小满 · 一键启动 AI 陪伴'
$taskShortcut.Save()
Write-Host "已创建桌面快捷方式：$taskDesktop\小满.lnk"
