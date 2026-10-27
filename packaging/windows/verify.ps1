#requires -Version 5.1
param([switch]$Headless)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'process_helpers.psm1') -Force

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runRoot = New-PfRunDirectory -Parent (Join-Path $projectRoot 'artifacts')
$reportPath = Join-Path $runRoot 'report.json'
$steps = New-Object 'System.Collections.Generic.List[object]'
$script:currentStep = 'platform'
$script:report = [ordered]@{
    format = 1; status = 'running'; current_step = 'platform'; failed_step = $null
    started_at_utc = [DateTime]::UtcNow.ToString('o'); completed_at_utc = $null
    stage3_complete = $false; windows11_manual_acceptance = 'pending'
    clean_machine_without_python = 'pending'; uses_real_financial_data = $false
    headless = [bool]$Headless; platform = $null; runtime = $null; uv_version = $null
    source_self_test = $null; frozen_self_test = $null; executable = $null
    steps = $steps
}
Write-PfJson -Path $reportPath -Value $script:report

function Invoke-VerificationStep {
    param([string]$Identifier, [string]$Executable, [string[]]$Arguments,
          [hashtable]$Environment = @{}, [int]$Timeout = 900)
    $script:currentStep = $Identifier
    $script:report.current_step = $Identifier
    Write-PfJson -Path $reportPath -Value $script:report
    Write-Host ('Проверка: ' + $Identifier)
    $result = Invoke-PfProcess -FilePath $Executable -ArgumentList $Arguments `
        -WorkingDirectory $projectRoot -LogPrefix (Join-Path $runRoot $Identifier) `
        -Step $Identifier -Environment $Environment -TimeoutSeconds $Timeout
    $steps.Add($result)
    Write-PfJson -Path $reportPath -Value $script:report
    if ($result.status -ne 'passed') { throw ('Проверка не пройдена: ' + $Identifier) }
}

try {
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        $script:report.status = 'unsupported_platform'
        throw 'Нужен запуск на Windows.'
    }
    $osInfo = Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/Windows NT/CurrentVersion'
    $build = [int]$osInfo.CurrentBuildNumber
    $displayVersion = $null
    if ($null -ne $osInfo.PSObject.Properties['DisplayVersion']) { $displayVersion = $osInfo.DisplayVersion }
    $isX64 = [Environment]::Is64BitProcess -and ($env:PROCESSOR_ARCHITECTURE -eq 'AMD64')
    $isTarget = $build -ge 22000 -and $osInfo.InstallationType -eq 'Client' -and $isX64
    $script:report.platform = [ordered]@{
        family = 'Windows'; build = $build; display_version = $displayVersion
        installation_type = $osInfo.InstallationType; process_x64 = $isX64
        target_windows11_x64 = $isTarget
        powershell = $PSVersionTable.PSVersion.ToString()
    }
    if (-not $isX64 -or (-not $isTarget -and -not $Headless)) {
        throw 'Для обычной проверки требуется Windows 11 и 64-битный PowerShell x64.'
    }
    $script:currentStep = 'uv_prerequisite'
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction Stop
    $environment = @{ PYTHONUTF8 = '1'; QT_QPA_PLATFORM = 'offscreen' }
    Invoke-VerificationStep '01-uv' $uvCommand.Source @('--version') $environment
    $script:report.uv_version = (Get-Content (Join-Path $runRoot '01-uv.stdout.log') -Raw).Trim()
    Invoke-VerificationStep '02-dependencies' $uvCommand.Source @('sync', '--frozen') $environment 1800
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    Invoke-VerificationStep '03-runtime' $python @('tools/runtime_info.py') $environment
    $script:report.runtime = Get-Content (Join-Path $runRoot '03-runtime.stdout.log') -Raw -Encoding UTF8 | ConvertFrom-Json
    $expectedPython = (Get-Content (Join-Path $projectRoot '.python-version') -Raw).Trim()
    if ($script:report.runtime.python -ne $expectedPython -or $script:report.runtime.pointer_bits -ne 64) {
        throw 'Версия или разрядность Python отличается от зафиксированной.'
    }
    $shellExecutable = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    Invoke-VerificationStep '03-process-helpers' $shellExecutable @('-NoProfile', '-NonInteractive',
        '-File', (Join-Path $PSScriptRoot 'test_helpers.ps1'), '-PythonPath', $python) $environment
    Invoke-VerificationStep '04-lint' $python @('-m', 'ruff', 'check', 'src', 'tests', 'tools') $environment
    Invoke-VerificationStep '05-format' $python @('-m', 'ruff', 'format', '--check', 'src', 'tests', 'tools') $environment
    Invoke-VerificationStep '06-types' $python @('-m', 'mypy', 'src/personal_finance') $environment
    Invoke-VerificationStep '07-tests' $python @('-m', 'pytest', '-q', '--junitxml', (Join-Path $runRoot 'tests.xml')) $environment 1200
    $qtPlatform = $(if ($Headless) { 'offscreen' } else { 'windows' })
    $nativeEnvironment = @{ PYTHONUTF8 = '1'; QT_QPA_PLATFORM = $qtPlatform }
    $sourceReport = Join-Path $runRoot 'source-self-test.json'
    Invoke-VerificationStep '08-source' $python @('-m', 'personal_finance.bootstrap.main',
        '--self-test', '--self-test-report', $sourceReport) $nativeEnvironment
    $script:report.source_self_test = Get-Content $sourceReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($script:report.source_self_test.status -ne 'passed' -or $script:report.source_self_test.runtime.frozen `
            -or $script:report.source_self_test.runtime.platform -ne 'win32' `
            -or $script:report.source_self_test.runtime.qt_platform -ne $qtPlatform) {
        throw 'Не подтверждена самопроверка исходников.'
    }
    $bundle = Join-Path $runRoot 'bundle'
    Invoke-VerificationStep '09-build' $shellExecutable @('-NoProfile', '-NonInteractive', '-File',
        (Join-Path $PSScriptRoot 'build.ps1'), '-OutputDirectory', $bundle,
        '-SkipDependencySync', '-QtPlatform', $qtPlatform) $nativeEnvironment 1800
    $buildResult = Get-Content (Join-Path $bundle 'build-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $script:report.frozen_self_test = Get-Content (Join-Path $bundle 'self-test.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($buildResult.status -ne 'passed' -or $script:report.frozen_self_test.status -ne 'passed' `
            -or -not $script:report.frozen_self_test.runtime.frozen) {
        throw 'Не подтверждена самопроверка собранного приложения.'
    }
    $script:report.executable = 'bundle/app/PersonalFinance/PersonalFinance.exe'
    $script:report.status = 'passed'
    Write-Host 'Автоматические проверки пройдены. Ручная проверка Windows 11 остаётся.'
    $exitCode = 0
} catch {
    if ($script:report.status -ne 'unsupported_platform') { $script:report.status = 'failed' }
    $script:report.failed_step = $script:currentStep
    $script:report['error_type'] = $_.Exception.GetType().Name
    Write-Host ('Проверка остановлена на этапе: ' + $script:currentStep)
    if ($script:currentStep -in @('platform', 'uv_prerequisite')) {
        Write-Host 'Нужны Windows 11 x64, 64-битный PowerShell и uv. Инструкция: docs/windows-quickstart.md'
    }
    $exitCode = 1
} finally {
    $script:report.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $script:report.current_step = $null
    Write-PfJson -Path $reportPath -Value $script:report
    Write-Host ('Отчёт: ' + $reportPath)
    Write-Host 'Файлы отчёта никуда не отправляются.'
}
exit $exitCode
