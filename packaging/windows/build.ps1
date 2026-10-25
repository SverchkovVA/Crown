$ErrorActionPreference = 'Stop'
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'Сборка Windows должна выполняться на Windows.'
}
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
Push-Location $ProjectRoot
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зафиксированные зависимости.' }
    uv run --frozen python -m PyInstaller --clean --noconfirm packaging/windows/personal_finance.spec
    if ($LASTEXITCODE -ne 0) { throw 'Сборка не завершена.' }
    $Executable = Join-Path $ProjectRoot 'dist/PersonalFinance/PersonalFinance.exe'
    $Process = Start-Process -FilePath $Executable -ArgumentList '--self-test' -PassThru -Wait
    if ($Process.ExitCode -ne 0) { throw 'Самопроверка собранного приложения не пройдена.' }
    Write-Output 'Каталог приложения: dist/PersonalFinance. Самопроверка пройдена.'
} finally {
    Pop-Location
}
