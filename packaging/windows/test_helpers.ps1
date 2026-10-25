#requires -Version 5.1
param([Parameter(Mandatory = $true)][string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'process_helpers.psm1') -Force
$python = (Resolve-Path -LiteralPath $PythonPath).Path

function Assert-PfCondition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$cases = @(
    @{ value = ''; expected = '""' },
    @{ value = 'two words'; expected = '"two words"' },
    @{ value = 'a"b'; expected = '"a\"b"' },
    @{ value = 'C:\folder\'; expected = '"C:\folder\\"' },
    @{ value = '$(&);'; expected = '"$(&);"' }
)
foreach ($case in $cases) {
    Assert-PfCondition ((ConvertTo-PfWindowsArgument $case.value) -ceq $case.expected) `
        'Нарушены правила экранирования аргументов Windows.'
}
$parseErrors = @()
Get-ChildItem $PSScriptRoot -Include *.ps1, *.psm1 -Recurse | ForEach-Object {
    $tokens = $null; $found = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$found)
    $parseErrors += $found
}
Assert-PfCondition ($parseErrors.Count -eq 0) 'Ошибка синтаксиса PowerShell.'

$testRoot = New-PfRunDirectory -Parent ([System.IO.Path]::GetTempPath()) -Prefix 'pf-helper-test'
try {
    $working = Join-Path $testRoot 'Путь с пробелами'
    [void](New-Item -ItemType Directory -Path $working)
    $arguments = @('', 'two words', 'Русский "текст"', 'C:\folder\', '$(&); | `literal')
    $program = 'import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=False))'
    $roundtrip = Invoke-PfProcess -FilePath $python -ArgumentList (@('-c', $program) + $arguments) `
        -WorkingDirectory $working -LogPrefix (Join-Path $testRoot 'arguments') `
        -Step 'arguments' -Environment @{PYTHONUTF8 = '1'} -TimeoutSeconds 20
    Assert-PfCondition ($roundtrip.status -eq 'passed') 'Не запустился дочерний процесс.'
    $decoded = @(Get-Content (Join-Path $testRoot 'arguments.stdout.log') -Raw -Encoding UTF8 | ConvertFrom-Json)
    Assert-PfCondition ($decoded.Count -eq $arguments.Count) 'Изменилось число аргументов.'
    for ($index = 0; $index -lt $arguments.Count; $index++) {
        Assert-PfCondition ($decoded[$index] -ceq $arguments[$index]) 'Аргумент изменился при передаче.'
    }
    $program = 'import sys; sys.stdout.write("x"*200000); sys.stderr.write("y"*200000)'
    $streams = Invoke-PfProcess -FilePath $python -ArgumentList @('-c', $program) `
        -WorkingDirectory $working -LogPrefix (Join-Path $testRoot 'streams') `
        -Step 'streams' -TimeoutSeconds 20
    Assert-PfCondition ($streams.status -eq 'passed') 'Чтение двух потоков заблокировалось.'
    Assert-PfCondition ((Get-Item (Join-Path $testRoot 'streams.stdout.log')).Length -eq 200000) 'Потерян stdout.'
    Assert-PfCondition ((Get-Item (Join-Path $testRoot 'streams.stderr.log')).Length -eq 200000) 'Потерян stderr.'
    $failed = Invoke-PfProcess -FilePath $python -ArgumentList @('-c', 'import sys; sys.exit(7)') `
        -WorkingDirectory $working -LogPrefix (Join-Path $testRoot 'failed') -Step 'failed'
    Assert-PfCondition ($failed.status -eq 'failed' -and $failed.exit_code -eq 7) 'Сбой ошибочно засчитан как успех.'
    $timeout = Invoke-PfProcess -FilePath $python -ArgumentList @('-c', 'import time; time.sleep(30)') `
        -WorkingDirectory $working -LogPrefix (Join-Path $testRoot 'timeout') -Step 'timeout' -TimeoutSeconds 1
    Assert-PfCondition ($timeout.timed_out -and $timeout.status -eq 'failed') 'Тайм-аут не обнаружен.'
    Write-Host 'PowerShell: аргументы, потоки, код ошибки, тайм-аут и синтаксис проверены.'
} finally {
    # Удаляются только собственные одноразовые данные с новым случайным именем.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
