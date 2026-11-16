#requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$BuildDirectory,
    [Parameter(Mandatory = $true)][string]$NsisArchive,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'process_helpers.psm1') -Force
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$build = (Resolve-Path -LiteralPath $BuildDirectory).Path
$archive = (Resolve-Path -LiteralPath $NsisArchive).Path
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputRoot) { throw 'Папка сборки установщика уже существует.' }
[void](New-Item -ItemType Directory -Path $outputRoot)
$archiveHash = 'ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8'
$receipt = [ordered]@{format=1;status='running';failed_step=$null;nsis='3.13';installer=$null}
$step = 'compiler'
try {
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $archiveHash) {
        throw 'Архив отличается от закреплённого NSIS 3.13.'
    }
    $compilerRoot = Join-Path $outputRoot 'compiler'
    Expand-Archive -LiteralPath $archive -DestinationPath $compilerRoot
    $nsis = Join-Path $compilerRoot 'nsis-3.13'
    $compiler = Join-Path $nsis 'makensis.exe'
    $python = Join-Path $project '.venv/Scripts/python.exe'
    $environment = @{PYTHONUTF8='1';PYTHONPATH=$null;PYTHONHOME=$null}
    $step = 'payload'
    $run = @{
        FilePath=$python; ArgumentList=@('-m','tools.windows_installer',$build,$outputRoot)
        WorkingDirectory=$project; LogPrefix=(Join-Path $outputRoot 'prepare')
        Step=$step; Environment=$environment; TimeoutSeconds=180
    }
    $prepared = Invoke-PfProcess @run
    if ($prepared.status -ne 'passed') { throw 'Не удалось проверить ведомость файлов.' }
    $compilerInput = Join-Path $outputRoot 'compiler-input'
    $manifestFile = Join-Path $compilerInput 'app-payload.json'
    $manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $sourceBuild = Get-Content -LiteralPath (Join-Path $compilerInput 'build-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $version = $manifest.application
    $fileVersion = $version -replace '\.dev', '.'
    if ($fileVersion.Split('.').Count -eq 3) { $fileVersion += '.0' }
    $name = 'Crown-' + $version + '-windows-x64-setup.exe'
    $outputFile = Join-Path $outputRoot $name
    $step = 'makensis'
    $arguments = @('/NOCONFIG', '/INPUTCHARSET', 'UTF8', '/WX',
        "/DCROWN_VERSION=$version", "/DCROWN_FILE_VERSION=$fileVersion", "/DCROWN_OUTPUT=$outputFile",
        ('/DCROWN_BUNDLE=' + (Join-Path $compilerInput 'app/PersonalFinance')),
        ('/DCROWN_GENERATED=' + (Join-Path $outputRoot 'payload.nsh')),
        "/DCROWN_MANIFEST=$manifestFile",
        ('/DCROWN_NSIS_LICENSE=' + (Join-Path $nsis 'COPYING')),
        (Join-Path $compilerInput 'crown.nsi'))
    $compilerEnvironment = @{
        NSISDIR=$nsis; NSISCONFDIR=$null
        PATH=(@($nsis, [Environment]::SystemDirectory, [Environment]::GetFolderPath('Windows')) -join [IO.Path]::PathSeparator)
    }
    $run = @{
        FilePath=$compiler; ArgumentList=$arguments
        WorkingDirectory=$outputRoot; LogPrefix=(Join-Path $outputRoot 'makensis')
        Step=$step; Environment=$compilerEnvironment; TimeoutSeconds=900
    }
    $compilation = Invoke-PfProcess @run
    if ($compilation.status -ne 'passed' -or -not (Test-Path -LiteralPath $outputFile -PathType Leaf)) {
        throw 'NSIS не собрал установщик.'
    }
    $step = 'windows_version_resource'
    $metadata = [Diagnostics.FileVersionInfo]::GetVersionInfo($outputFile)
    if ($metadata.ProductName -cne 'Crown' -or $metadata.ProductVersion -cne $version -or
        $metadata.FileVersion -cne $version) {
        throw 'Версия файла установщика не совпадает с версией поставки.'
    }
    $receipt.status='passed'
    $receipt['windows_product_version']=$metadata.ProductVersion
    $receipt.installer=$name
    $receipt['application']=$version
    $receipt['nsis_archive_sha256']=$archiveHash
    $receipt['nsis_exe_sha256']=(Get-FileHash -LiteralPath $compiler).Hash.ToLowerInvariant()
    $receipt['installer_sha256']=(Get-FileHash -LiteralPath $outputFile).Hash.ToLowerInvariant()
    $receipt['payload_sha256']=(Get-FileHash -LiteralPath $manifestFile).Hash.ToLowerInvariant()
    $receipt['payload_files']=$manifest.file_count
    $receipt['source_sha256']=$sourceBuild.source_sha256
    Write-PfJson -Path (Join-Path $outputRoot 'installer-build.json') -Value $receipt
    Write-Host ('Установщик: ' + $name)
    exit 0
} catch {
    $receipt.status='failed'
    $receipt.failed_step=$step
    $receipt['error_type']=$_.Exception.GetType().Name
    Write-PfJson -Path (Join-Path $outputRoot 'installer-build.json') -Value $receipt
    Write-Host ('Сборка установщика остановлена на шаге ' + $step)
    exit 1
}

