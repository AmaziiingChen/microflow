Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $RepoRoot ".venv-pack-win"
$PythonLauncher = "py"
$PreferredPythonVersions = @("3.12", "3.11", "3.13")
$PyInstallerConfigDir = Join-Path $RepoRoot ".pyinstaller-cache"
$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$InnoScript = Join-Path $RepoRoot "packaging\windows\MicroFlow.iss"
$DistDir = Join-Path $RepoRoot "dist"
$DistAppDir = Join-Path $DistDir "MicroFlow"
$ReleaseDir = Join-Path $RepoRoot "release\windows"
$SetupExe = Join-Path $ReleaseDir "MicroFlow-Setup-v1.0.0.exe"

Write-Host "==> Repo root: $RepoRoot"
Set-Location $RepoRoot

Write-Host "==> Resolving supported Python version via py launcher"
$PythonVersion = $null
foreach ($candidate in $PreferredPythonVersions) {
    try {
        & $PythonLauncher "-$candidate" "--version" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $PythonVersion = $candidate
            break
        }
    } catch {
        continue
    }
}

if (-not $PythonVersion) {
    throw "No supported Python found. Install one of: $($PreferredPythonVersions -join ', ')"
}

Write-Host "==> Using Python $PythonVersion"

Write-Host "==> Recreating clean packaging venv"
if (Test-Path $VenvPath) {
    Remove-Item $VenvPath -Recurse -Force
}
& $PythonLauncher "-$PythonVersion" -m venv $VenvPath

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (!(Test-Path $VenvPython)) {
    throw "Packaging venv was not created correctly: $VenvPython"
}

Write-Host "==> Installing packaging dependencies"
& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -r (Join-Path $RepoRoot "requirements-packaging.txt")

Write-Host "==> Building release icons"
& $VenvPython (Join-Path $RepoRoot "scripts\build_release_icons.py")

$IconIco = Join-Path $RepoRoot "frontend\icons\icon.ico"
if (!(Test-Path $IconIco)) {
    throw "Missing generated icon: $IconIco"
}

Write-Host "==> Cleaning old build artifacts"
foreach ($path in @($DistDir, (Join-Path $RepoRoot "build"), $ReleaseDir)) {
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
    }
}

Write-Host "==> Running PyInstaller"
$env:PYINSTALLER_CONFIG_DIR = $PyInstallerConfigDir
& $VenvPython -m PyInstaller --clean --noconfirm (Join-Path $RepoRoot "MicroFlow.windows.spec")

if (!(Test-Path $DistAppDir)) {
    throw "PyInstaller output directory not found: $DistAppDir"
}

if (!(Test-Path $InnoCompiler)) {
    throw "Inno Setup compiler not found: $InnoCompiler"
}

Write-Host "==> Running Inno Setup"
& $InnoCompiler `
  $InnoScript `
  "/DMyAppVersion=v1.0.0" `
  "/DMySourceDir=$DistAppDir" `
  "/DMyOutputDir=$ReleaseDir"

if (!(Test-Path $SetupExe)) {
    throw "Expected installer not found: $SetupExe"
}

Write-Host "==> Release files"
Get-ChildItem $ReleaseDir | Format-Table Name, Length

Write-Host "==> SHA256"
Get-FileHash $SetupExe -Algorithm SHA256
