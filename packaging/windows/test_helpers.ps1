#requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [switch]$EncodingProbe
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'process_helpers.psm1') -Force
if ($EncodingProbe) {
    Write-Output 'Проверка русского stdout'
    Write-Error 'Проверка русского stderr' -ErrorAction Continue
    exit 0
}
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
    # В PowerShell 5.1 JSON-массив выходит из конвейера одним объектом.
    # Присваивание типизированной переменной не добавляет внешний массив, как @(...).
    [object[]]$decoded = ConvertFrom-Json -InputObject (
        [System.IO.File]::ReadAllText((Join-Path $testRoot 'arguments.stdout.log'), [System.Text.Encoding]::UTF8)
    )
    Assert-PfCondition ($decoded.Count -eq $arguments.Count) (
        'Изменилось число аргументов: ожидалось {0}, получено {1}.' -f $arguments.Count, $decoded.Count
    )
    for ($index = 0; $index -lt $arguments.Count; $index++) {
        Assert-PfCondition ($decoded[$index] -ceq $arguments[$index]) 'Аргумент изменился при передаче.'
    }
    $shell = (Get-Process -Id $PID).Path
    $encodingResult = Invoke-PfProcess -FilePath $shell -ArgumentList @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-File', $PSCommandPath,
        '-PythonPath', $python, '-EncodingProbe'
    ) -WorkingDirectory $working -LogPrefix (Join-Path $testRoot 'encoding') `
        -Step 'encoding' -TimeoutSeconds 20
    Assert-PfCondition ($encodingResult.status -eq 'passed') 'Не запустилась проверка кодировки PowerShell.'
    $outText = [System.IO.File]::ReadAllText((Join-Path $testRoot 'encoding.stdout.log'), [System.Text.Encoding]::UTF8)
    $errText = [System.IO.File]::ReadAllText((Join-Path $testRoot 'encoding.stderr.log'), [System.Text.Encoding]::UTF8)
    Assert-PfCondition ($outText.Trim() -ceq 'Проверка русского stdout') 'Искажена кириллица в stdout PowerShell.'
    # Write-Error переносит даже слово при длинном пути сценария.
    $compactError = $errText -replace '\s', ''
    Assert-PfCondition ($compactError.Contains('Проверкарусскогоstderr')) 'Искажена кириллица в stderr PowerShell.'
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
    Write-Host 'PowerShell: аргументы, кодировка, потоки, код ошибки, тайм-аут и синтаксис проверены.'
} finally {
    # Удаляются только собственные одноразовые данные с новым случайным именем.
    $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
    $expectedParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/')
    if ([System.IO.Path]::GetDirectoryName($resolvedTestRoot) -cne $expectedParent -or
        [System.IO.Path]::GetFileName($resolvedTestRoot) -notlike 'pf-helper-test-*' -or
        ((Get-Item -LiteralPath $resolvedTestRoot -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'Отказ от удаления каталога за пределами собственных временных данных.'
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}
