[CmdletBinding()]
param(
    [string]$FnpackPath,
    [string]$PythonPath,
    [string]$OutputDirectory,
    [switch]$SkipRuntimeBuild
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'dist' }
$packageRoot = Join-Path $projectRoot 'fnos'
$appRoot = Join-Path $packageRoot 'app'
$fnpackVersion = '1.2.3'

if (-not $SkipRuntimeBuild) {
    $pythonArguments = @()
    if (-not $PythonPath) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            $PythonPath = $launcher.Source
            $pythonArguments = @('-3.12')
        } else {
            $PythonPath = 'python'
        }
    }
    & $PythonPath @pythonArguments (Join-Path $PSScriptRoot 'build-runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Native runtime preparation failed.' }
    & $PythonPath @pythonArguments (Join-Path $PSScriptRoot 'check-runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Native runtime dependency audit failed.' }
}
foreach ($command in @('python3', 'svn', 'svnadmin', 'svnlook', 'svnserve')) {
    $binary = Join-Path $appRoot "runtime\bin\$command"
    if (-not (Test-Path -LiteralPath $binary)) { throw "Missing native runtime: $binary" }
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'server.py') -Destination $appRoot -Force
$stagedStatic = Join-Path $appRoot 'static'
$resolvedStagedStatic = [System.IO.Path]::GetFullPath($stagedStatic)
$allowedStageRoot = [System.IO.Path]::GetFullPath($appRoot) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedStagedStatic.StartsWith($allowedStageRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Static staging directory is outside the expected package application directory.'
}
if (Test-Path -LiteralPath $stagedStatic) { Remove-Item -LiteralPath $stagedStatic -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $projectRoot 'static') -Destination $appRoot -Recurse

if (-not $FnpackPath) {
    $installed = Get-Command fnpack -ErrorAction SilentlyContinue
    if ($installed) {
        $FnpackPath = $installed.Source
    } else {
        $cacheDirectory = Join-Path $env:LOCALAPPDATA 'FnSVN\tools'
        New-Item -ItemType Directory -Path $cacheDirectory -Force | Out-Null
        $FnpackPath = Join-Path $cacheDirectory "fnpack-$fnpackVersion.exe"
        if (-not (Test-Path -LiteralPath $FnpackPath)) {
            $downloadUrl = "https://static2.fnnas.com/fnpack/fnpack-$fnpackVersion-windows-amd64"
            Invoke-WebRequest -Uri $downloadUrl -OutFile $FnpackPath -UseBasicParsing
        }
    }
}
$FnpackPath = (Resolve-Path -LiteralPath $FnpackPath).Path
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$utf8 = New-Object System.Text.UTF8Encoding($false)
$packageTextFiles = @(
    (Join-Path $packageRoot 'manifest'),
    (Join-Path $packageRoot 'config\privilege'),
    (Join-Path $packageRoot 'config\resource'),
    (Join-Path $appRoot 'ui\config'),
    (Join-Path $appRoot 'prepare-runtime.sh')
) + @(Get-ChildItem (Join-Path $packageRoot 'cmd') -File | ForEach-Object { $_.FullName }) +
    @(Get-ChildItem (Join-Path $packageRoot 'wizard') -File | Where-Object { $_.Name -ne '.gitkeep' } | ForEach-Object { $_.FullName })
foreach ($path in $packageTextFiles) {
    $content = [System.IO.File]::ReadAllText($path).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($path, $content, $utf8)
}
$builtPackage = Join-Path $OutputDirectory 'fnsvn.fpk'
if (Test-Path -LiteralPath $builtPackage) { Remove-Item -LiteralPath $builtPackage -Force }
Push-Location $OutputDirectory
try {
    & $FnpackPath build --directory $packageRoot
    if ($LASTEXITCODE -ne 0) { throw "fnpack failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
if (-not (Test-Path -LiteralPath $builtPackage)) { throw "fnpack did not produce $builtPackage" }
Get-Item -LiteralPath $builtPackage
Get-FileHash -LiteralPath $builtPackage -Algorithm SHA256
Write-Host 'Native x86-64 FPK includes Python and SVN. Validate installation and lifecycle on a real fnOS device.'
