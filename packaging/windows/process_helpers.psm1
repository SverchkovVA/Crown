#requires -Version 5.1
Set-StrictMode -Version Latest
# Собственные PowerShell-процессы пишут в той же кодировке, в которой читаются
# их перенаправленные потоки. Системная кодовая страница не изменяется.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-PfWindowsArgument {
    param([AllowEmptyString()][Parameter(Mandatory = $true)][string]$Value)
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $slashes + 1)))
            [void]$builder.Append('"')
        } else {
            [void]$builder.Append(('\' * $slashes))
            [void]$builder.Append($character)
        }
        $slashes = 0
    }
    [void]$builder.Append(('\' * (2 * $slashes)))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Write-PfJson {
    param([Parameter(Mandatory = $true)][string]$Path,
          [Parameter(Mandatory = $true)]$Value)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), $encoding)
}

function New-PfRunDirectory {
    param([Parameter(Mandatory = $true)][string]$Parent, [string]$Prefix = 'windows-check')
    [void][System.IO.Directory]::CreateDirectory($Parent)
    $suffix = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N')
    $path = Join-Path $Parent ($Prefix + '-' + $suffix)
    [void](New-Item -ItemType Directory -Path $path -ErrorAction Stop)
    return [System.IO.Path]::GetFullPath($path)
}

function Invoke-PfProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogPrefix,
        [Parameter(Mandatory = $true)][string]$Step,
        [hashtable]$Environment = @{},
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 900
    )
    $stdoutPath = $LogPrefix + '.stdout.log'
    $stderrPath = $LogPrefix + '.stderr.log'
    if ((Test-Path -LiteralPath $stdoutPath) -or (Test-Path -LiteralPath $stderrPath)) {
        throw 'Файлы журнала уже существуют; повторное использование папки запрещено.'
    }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $FilePath
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $start.StandardOutputEncoding = $encoding
    $start.StandardErrorEncoding = $encoding
    if ($null -ne $start.PSObject.Properties['ArgumentList']) {
        foreach ($argument in $ArgumentList) { $start.ArgumentList.Add($argument) }
    } else {
        $start.Arguments = (($ArgumentList | ForEach-Object {
            ConvertTo-PfWindowsArgument -Value $_
        }) -join ' ')
    }
    foreach ($name in $Environment.Keys) {
        if ($null -eq $Environment[$name]) { $start.EnvironmentVariables.Remove($name) }
        else { $start.EnvironmentVariables[$name] = [string]$Environment[$name] }
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            $treeKill = $process.GetType().GetMethod('Kill', [Type[]]@([bool]))
            if ($null -ne $treeKill) { $process.Kill($true) }
            elseif ($env:OS -eq 'Windows_NT') {
                $kill = New-Object System.Diagnostics.ProcessStartInfo
                $kill.FileName = Join-Path $env:WINDIR 'System32/taskkill.exe'
                $kill.Arguments = '/PID ' + $process.Id + ' /T /F'
                $kill.UseShellExecute = $false
                $kill.CreateNoWindow = $true
                $killer = [System.Diagnostics.Process]::Start($kill)
                try { [void]$killer.WaitForExit(10000) } finally { $killer.Dispose() }
            } else { $process.Kill() }
            if (-not $process.WaitForExit(10000)) { throw 'Процесс не завершился после тайм-аута.' }
        }
        if (-not $stdout.Wait(10000) -or -not $stderr.Wait(10000)) {
            throw 'Не завершилось чтение журналов процесса.'
        }
        [System.IO.File]::WriteAllText($stdoutPath, $stdout.Result, $encoding)
        [System.IO.File]::WriteAllText($stderrPath, $stderr.Result, $encoding)
        return [pscustomobject]@{
            id = $Step
            status = $(if ($timedOut -or $process.ExitCode -ne 0) { 'failed' } else { 'passed' })
            exit_code = $process.ExitCode
            timed_out = $timedOut
            duration_ms = $watch.ElapsedMilliseconds
            stdout_log = [System.IO.Path]::GetFileName($stdoutPath)
            stderr_log = [System.IO.Path]::GetFileName($stderrPath)
        }
    } finally { $process.Dispose() }
}

Export-ModuleMember -Function ConvertTo-PfWindowsArgument, Write-PfJson, New-PfRunDirectory, Invoke-PfProcess
