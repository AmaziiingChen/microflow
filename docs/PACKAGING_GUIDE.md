# MicroFlow 打包指南

更新时间：2026-04-01

本指南用于整理 MicroFlow 当前可执行的本地打包流程，重点覆盖：

- macOS `arm64` 打包
- Windows `x64` 打包
- 发布图标生成
- 无代码签名时的分发方式

## 1. 发布图标

项目当前使用以下图标策略：

- 应用图标：`frontend/icons/icon.png`
- macOS 菜单栏托盘图标：仍使用 `frontend/icons/icon_white.png`
- Windows 托盘图标：默认使用彩色 `icon.png`

打包前先生成发布图标：

```bash
python3 scripts/build_release_icons.py
```

生成结果：

- `frontend/icons/icon.ico`
- `frontend/icons/icon.icns`（仅 macOS）

## 2. macOS 打包

当前主开发机为 Apple Silicon，建议在专用虚拟环境中打包：

```bash
cd /Users/chen/Code/MicroFlow

python3 -m venv .venv-pack-macos
source .venv-pack-macos/bin/activate

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-packaging.txt

python scripts/build_release_icons.py

# 强烈建议：先在你的开发环境 .venv 中完成校验，再切回这个干净的打包环境
# source .venv/bin/activate
# node --check frontend/js/app.js
# python -m py_compile main.py src/api.py src/database.py src/core/scheduler.py src/services/config_service.py src/services/telemetry_service.py src/llm_service.py
# pytest -q
# deactivate

rm -rf build dist release/macos
python -m PyInstaller --clean --noconfirm MicroFlow.spec

mkdir -p release/macos
ditto -c -k --sequesterRsrc --keepParent dist/MicroFlow.app release/macos/MicroFlow-v1.0.0-macos-arm64.zip
./scripts/package_macos_dmg.sh dist/MicroFlow.app v1.0.0 arm64

shasum -a 256 release/macos/MicroFlow-v1.0.0-macos-arm64.zip
stat -f%z release/macos/MicroFlow-v1.0.0-macos-arm64.zip
shasum -a 256 release/macos/MicroFlow-v1.0.0-macos-arm64.dmg
stat -f%z release/macos/MicroFlow-v1.0.0-macos-arm64.dmg
```

说明：

- 当前 `MicroFlow.spec` 仅面向 macOS `arm64`
- `scripts/package_macos_dmg.sh` 会先构建一个临时 DMG 目录，里面包含：
  - `MicroFlow.app`
  - `/Applications` 的符号链接
- Finder 打开该 DMG 后，会出现标准的“把应用拖到 Applications”安装目标，不再是只有一个孤立的 app 图标
- 发布包不再内置 Playwright Chromium，运行时将优先调用系统已安装的 Chrome / Edge / Chromium
- 当前未签名、未公证，用户首次启动需使用“右键打开”或在“隐私与安全性”中手动放行
- 发布时建议优先上传 `dmg`，`zip` 作为备用包

## 3. Windows 打包

Windows 必须在 Windows 机器上执行，当前仓库已提供：

- `MicroFlow.windows.spec`
- `packaging/windows/MicroFlow.iss`

建议在 Windows 专用虚拟环境中打包。当前基线为 Python `3.11`；如果本机没有 `3.11`，再考虑使用 `3.12` 或 `3.13`。不建议使用 Python `3.14`：

说明：当前 Windows 打包基线为 Python `3.11`；如果本机没有 `3.11`，请先安装后再执行下面命令。

```powershell
cd C:\path\to\MicroFlow

py -3.11 -m venv .venv-pack-win

.\.venv-pack-win\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-pack-win\Scripts\python.exe -m pip install -r requirements-packaging.txt

.\.venv-pack-win\Scripts\python.exe scripts\build_release_icons.py

# 强烈建议：先在开发环境里完成校验，再回到这个干净的打包环境执行 PyInstaller
# node --check frontend\js\app.js
# python -m py_compile main.py src\api.py src\database.py src\core\scheduler.py src\services\config_service.py src\services\telemetry_service.py src\llm_service.py
# pytest -q

if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path release\windows) { Remove-Item release\windows -Recurse -Force }

.\.venv-pack-win\Scripts\python.exe -m PyInstaller --clean --noconfirm MicroFlow.windows.spec
```

如果你不想手动敲完整套命令，也可以直接运行：

```powershell
.\scripts\package_windows_release.ps1
```

如果本机已安装 Inno Setup 6，可继续生成正式安装包：

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
  "packaging\windows\MicroFlow.iss" `
  "/DMyAppVersion=v1.0.0" `
  "/DMySourceDir=$PWD\dist\MicroFlow" `
  "/DMyOutputDir=$PWD\release\windows"
```

然后计算安装包摘要：

```powershell
Get-FileHash .\release\windows\MicroFlow-Setup-v1.0.0.exe -Algorithm SHA256
(Get-Item .\release\windows\MicroFlow-Setup-v1.0.0.exe).Length
```

说明：

- `MicroFlow.windows.spec` 采用单目录产物，适合继续交给 Inno Setup 生成 `.exe` 安装包
- 发布包不再内置 Playwright Chromium，运行时默认优先调用系统自带的 Microsoft Edge
- 当前未签名，Windows SmartScreen 首次运行时会弹警告，这是正常现象
- 正式分发前建议在一台干净 Windows 机器上完整测试安装、首次启动、托盘、更新检查和抓取

## 4. 远程版本文件回填

打包完成后，将真实下载地址、文件大小和 SHA256 回填到：

- `version.json`

至少更新这些字段：

- `downloads.windows.url`
- `downloads.windows.sha256`
- `downloads.windows.size`
- `downloads.macos.url`
- `downloads.macos.sha256`
- `downloads.macos.size`
- `release_date`
- `notes`

再将根目录 `version.json` 上传到腾讯云 COS。
