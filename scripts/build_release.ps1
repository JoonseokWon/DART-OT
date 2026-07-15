param(
    [string]$Version = "dev",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildRoot = Join-Path $Root "build"
$DistRoot = Join-Path $BuildRoot "release-bin"
$WorkRoot = Join-Path $BuildRoot "pyinstaller"
$SpecRoot = Join-Path $BuildRoot "spec"
$ReleaseRoot = Join-Path $Root "release"
$SafeVersion = $Version -replace '[^0-9A-Za-z._-]', '-'
$PackageName = "DART-OT-$SafeVersion-windows-x64"
$PackageRoot = Join-Path $ReleaseRoot $PackageName
$ZipPath = Join-Path $ReleaseRoot "$PackageName.zip"
$ChecksumPath = "$ZipPath.sha256"

function Remove-GeneratedPath([string]$Path) {
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $AllowedRoots = @(
        [System.IO.Path]::GetFullPath($BuildRoot),
        [System.IO.Path]::GetFullPath($ReleaseRoot)
    )
    if (-not ($AllowedRoots | Where-Object { $FullPath.StartsWith($_ + [System.IO.Path]::DirectorySeparatorChar) })) {
        throw "Refusing to remove path outside generated directories: $FullPath"
    }
    if (Test-Path -LiteralPath $FullPath) {
        Remove-Item -LiteralPath $FullPath -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot, $ReleaseRoot | Out-Null
Remove-GeneratedPath $PackageRoot
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
if (Test-Path -LiteralPath $ChecksumPath) { Remove-Item -LiteralPath $ChecksumPath -Force }

$Targets = @(
    @{ Name = "DART-OT"; Script = "app.py"; NeedsTk = $true },
    @{ Name = "DART-Disclosure-Viewer"; Script = "disclosure_viewer.py"; NeedsTk = $false },
    @{ Name = "DART-DEP"; Script = "depreciation_app.py"; NeedsTk = $true }
)

foreach ($Target in $Targets) {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name $Target.Name `
        --distpath $DistRoot `
        --workpath (Join-Path $WorkRoot $Target.Name) `
        --specpath $SpecRoot `
        (Join-Path $Root $Target.Script)
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed for $($Target.Name)"
    }
    if ($Target.NeedsTk) {
        $WarningPath = Join-Path (Join-Path $WorkRoot $Target.Name) "$($Target.Name)\warn-$($Target.Name).txt"
        if ((Test-Path -LiteralPath $WarningPath) -and (Select-String -LiteralPath $WarningPath -Pattern "missing module named tkinter" -Quiet)) {
            throw "PyInstaller excluded tkinter for $($Target.Name). Use a Python installation that includes Tcl/Tk."
        }
    }
}

New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null
foreach ($Target in $Targets) {
    $Executable = Join-Path $DistRoot "$($Target.Name).exe"
    if (-not (Test-Path -LiteralPath $Executable)) {
        throw "Missing executable: $Executable"
    }
    if ((Get-Item -LiteralPath $Executable).Length -lt 5MB) {
        throw "Executable is unexpectedly small: $Executable"
    }
    Copy-Item -LiteralPath $Executable -Destination $PackageRoot
}

Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination $PackageRoot
Copy-Item -LiteralPath (Join-Path $Root "프로세스.md") -Destination $PackageRoot
Set-Content -LiteralPath (Join-Path $PackageRoot "VERSION.txt") -Value $Version -Encoding UTF8

Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $ChecksumPath -Value "$Hash  $([System.IO.Path]::GetFileName($ZipPath))" -Encoding ASCII

Write-Output "Release package: $ZipPath"
Write-Output "SHA256: $Hash"
