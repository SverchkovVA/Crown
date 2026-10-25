#requires -Version 5.1
param(
    [string]$OutputDirectory = '',
    [switch]$SkipDependencySync,
    [ValidateSet('windows', 'offscreen')][string]$QtPlatform = 'windows'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'process_helpers.psm1') -Force
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'Сборка Windows должна выполняться на Windows.'
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $outputRoot = New-PfRunDirectory -Parent (Join-Path $projectRoot 'artifacts') -Prefix 'windows-build'
} else {
    $outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
    if (Test-Path -LiteralPath $outputRoot) { throw 'Папка сборки уже существует.' }
    [void](New-Item -ItemType Directory -Path $outputRoot -ErrorAction Stop)
}
$record = [ordered]@{ format = 1; status = 'running'; failed_step = $null; executable = $null }
$currentStep = 'dependencies'
try {
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction Stop
    $environment = @{ PYTHONUTF8 = '1'; QT_QPA_PLATFORM = $QtPlatform }
    if (-not $SkipDependencySync) {
        $sync = Invoke-PfProcess -FilePath $uvCommand.Source -ArgumentList @('sync', '--frozen') `
            -WorkingDirectory $projectRoot -LogPrefix (Join-Path $outputRoot 'sync') `
            -Step 'dependencies' -Environment $environment -TimeoutSeconds 1800
        if ($sync.status -ne 'passed') { throw 'Установка зависимостей завершилась ошибкой.' }
    }
    $currentStep = 'pyinstaller'
    Write-Host 'Сборка в новую папку…'
    $arguments = @('run', '--frozen', 'python', '-m', 'PyInstaller',
        '--distpath', (Join-Path $outputRoot 'app'), '--workpath', (Join-Path $outputRoot 'work'),
        'packaging/windows/personal_finance.spec')
    $build = Invoke-PfProcess -FilePath $uvCommand.Source -ArgumentList $arguments `
        -WorkingDirectory $projectRoot -LogPrefix (Join-Path $outputRoot 'build') `
        -Step 'pyinstaller' -Environment $environment -TimeoutSeconds 1800
    if ($build.status -ne 'passed') { throw 'Не удалось собрать приложение.' }
    $currentStep = 'frozen_self_test'
    $executable = Join-Path $outputRoot 'app/PersonalFinance/PersonalFinance.exe'
    $reportPath = Join-Path $outputRoot 'self-test.json'
    $frozenEnvironment = @{
        PYTHONUTF8 = '1'; QT_QPA_PLATFORM = $QtPlatform; PYTHONPATH = $null; PYTHONHOME = $null
    }
    $check = Invoke-PfProcess -FilePath $executable `
        -ArgumentList @('--self-test', '--self-test-report', $reportPath) `
        -WorkingDirectory $outputRoot -LogPrefix (Join-Path $outputRoot 'self-test') `
        -Step 'frozen_self_test' -Environment $frozenEnvironment -TimeoutSeconds 180
    if ($check.status -ne 'passed') { throw 'Не пройдена самопроверка собранного приложения.' }
    $selfReport = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($selfReport.status -ne 'passed' -or -not $selfReport.runtime.frozen `
            -or $selfReport.runtime.platform -ne 'win32' `
            -or $selfReport.runtime.qt_platform -ne $QtPlatform) {
        throw 'Самопроверка не подтверждает запуск Windows-сборки.'
    }
    $record.status = 'passed'
    $record.executable = 'app/PersonalFinance/PersonalFinance.exe'
    $record['exe_sha256'] = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    $record['self_test_report'] = 'self-test.json'
    Write-PfJson -Path (Join-Path $outputRoot 'build-result.json') -Value $record
    Write-Host ('Каталог приложения: ' + (Join-Path $outputRoot 'app/PersonalFinance'))
    exit 0
} catch {
    $record.status = 'failed'
    $record.failed_step = $currentStep
    $record['error_type'] = $_.Exception.GetType().Name
    Write-PfJson -Path (Join-Path $outputRoot 'build-result.json') -Value $record
    Write-Host ('Сборка не завершена. Этап: ' + $currentStep)
    Write-Host ('Журналы: ' + $outputRoot)
    exit 1
}
