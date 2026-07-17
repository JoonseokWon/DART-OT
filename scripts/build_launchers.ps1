param(
    [string]$Compiler = ""
)

$ErrorActionPreference = "Stop"
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if (-not $Compiler) {
    $CompilerCandidates = @(
        "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
    )
    $Compiler = $CompilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) {
    throw "C# compiler was not found."
}

$Targets = @(
    @{
        Source = "Launcher.cs"
        Output = "DART-OT.exe"
        Icon = "assets\DART-OT.ico"
    },
    @{
        Source = "DisclosureViewerLauncher.cs"
        Output = "DART-Disclosure-Viewer.exe"
        Icon = "assets\DART-Disclosure-Viewer.ico"
    }
)

foreach ($Target in $Targets) {
    $SourcePath = Join-Path $Root $Target.Source
    $OutputPath = Join-Path $Root $Target.Output
    $IconPath = Join-Path $Root $Target.Icon
    foreach ($RequiredPath in @($SourcePath, $IconPath)) {
        if (-not (Test-Path -LiteralPath $RequiredPath)) {
            throw "Missing launcher input: $RequiredPath"
        }
    }

    & $Compiler `
        /nologo `
        /target:winexe `
        /optimize+ `
        /reference:System.Windows.Forms.dll `
        "/win32icon:$IconPath" `
        "/out:$OutputPath" `
        $SourcePath
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher build failed: $($Target.Output)"
    }
    Write-Output "Built launcher: $OutputPath"
}
