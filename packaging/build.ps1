<#
.SYNOPSIS
  One command: dist\StarExtractor\ (PyInstaller one-folder build), its self-tests, the Inno Setup installer,
  a portable zip and SHA256SUMS.txt in dist\release\.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python .venv\Scripts\python.exe

  Needs: Python with the project installed (pip install -e ".[dev,build]") and Inno Setup 6 (ISCC.exe).
#>
param(
    [string]$Python = "python",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = (Select-String -Path pyproject.toml -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Star ExTractor $Version"

# 1. PyInstaller one-folder build
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\starex_extractor.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$App = Join-Path $Root "dist\StarExtractor\StarExtractor.exe"

# 2. Self-tests on the FROZEN build (the exe is a windowed program: wait for it and read its exit code)
function Invoke-Frozen([string]$Arg) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo $App, $Arg
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $p.WaitForExit()
    Write-Host $out
    if ($p.ExitCode -ne 0) { throw "StarExtractor.exe $Arg failed (exit $($p.ExitCode))" }
}
Invoke-Frozen "--selftest"
Invoke-Frozen "--selftest-gui"

$Out = Join-Path $Root "dist\release"
New-Item -ItemType Directory -Force $Out | Out-Null

# 3. Portable zip of the one-folder build
$Zip = Join-Path $Out "StarExtractor-$Version-portable.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path "dist\StarExtractor" -DestinationPath $Zip -CompressionLevel Optimal

# 4. Installer
if (-not $SkipInstaller) {
    $Iscc = @(
        (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $Iscc) { throw "Inno Setup 6 (ISCC.exe) not found: winget install JRSoftware.InnoSetup" }
    & $Iscc "/DAppVersion=$Version" "/DSourceDir=$Root\dist\StarExtractor" "/DOutputDir=$Out" packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
}

# 5. Checksums
$Sums = Get-ChildItem $Out -File | Where-Object { $_.Extension -in ".exe", ".zip" } | ForEach-Object {
    "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower())  $($_.Name)"
}
Set-Content -Path (Join-Path $Out "SHA256SUMS.txt") -Value $Sums -Encoding ascii
Get-ChildItem $Out | Format-Table Name, @{n = "MB"; e = { "{0:N1}" -f ($_.Length / 1MB) } } -AutoSize
