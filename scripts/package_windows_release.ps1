Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $RepoRoot ".venv-pack-win"
$PythonLauncher = "py"
$PreferredPythonVersions = @("3.11", "3.12", "3.13")
$TempBuildRoot = Join-Path $env:TEMP "MicroFlow-package-windows"
$PyInstallerConfigDir = Join-Path $TempBuildRoot "pyinstaller-config"
$PyInstallerWorkDir = Join-Path $TempBuildRoot "pyinstaller-work"
$PyInstallerDistDir = Join-Path $TempBuildRoot "pyinstaller-dist"
$InnoOutputTempDir = Join-Path $TempBuildRoot "inno-output"
$InnoCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$InnoScript = Join-Path $RepoRoot "packaging\windows\MicroFlow.iss"
$DistDir = Join-Path $RepoRoot "dist"
$DistAppDir = Join-Path $DistDir "MicroFlow"
$TempDistAppDir = Join-Path $PyInstallerDistDir "MicroFlow"
$ReleaseDir = Join-Path $RepoRoot "release\windows"
$SetupExe = Join-Path $ReleaseDir "MicroFlow-Setup-v1.0.0.exe"
$TempSetupExe = Join-Path $InnoOutputTempDir "MicroFlow-Setup-v1.0.0.exe"

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
foreach ($path in @($PyInstallerConfigDir, $PyInstallerWorkDir, $PyInstallerDistDir, $InnoOutputTempDir)) {
    if (Test-Path $path) {
        Remove-Item $path -Recurse -Force
    }
}

Write-Host "==> Running PyInstaller"
$env:PYINSTALLER_CONFIG_DIR = $PyInstallerConfigDir
& $VenvPython -m PyInstaller --clean --noconfirm `
  --workpath $PyInstallerWorkDir `
  --distpath $PyInstallerDistDir `
  (Join-Path $RepoRoot "MicroFlow.windows.spec")

if (!(Test-Path $TempDistAppDir)) {
    throw "PyInstaller output directory not found: $TempDistAppDir"
}

if (!(Test-Path $InnoCompiler)) {
    throw "Inno Setup compiler not found: $InnoCompiler"
}

Write-Host "==> Running Inno Setup"
& $InnoCompiler `
  $InnoScript `
  "/DMyAppVersion=v1.0.0" `
  "/DMySourceDir=$TempDistAppDir" `
  "/DMyOutputDir=$InnoOutputTempDir"

if (!(Test-Path $TempSetupExe)) {
    throw "Expected installer not found: $TempSetupExe"
}

Write-Host "==> Copying build artifacts back to repo"
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
Copy-Item -LiteralPath $TempDistAppDir -Destination $DistAppDir -Recurse -Force
Copy-Item -LiteralPath $TempSetupExe -Destination $SetupExe -Force

if (!(Test-Path $SetupExe)) {
    throw "Expected installer not found: $SetupExe"
}

Write-Host "==> Release files"
Get-ChildItem $ReleaseDir | Format-Table Name, Length

Write-Host "==> SHA256"
Get-FileHash $SetupExe -Algorithm SHA256
