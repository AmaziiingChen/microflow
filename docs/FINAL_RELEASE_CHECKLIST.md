# MicroFlow 最终发布清单

更新时间：2026-04-01

本清单面向你当前的实际发布方式：

- 本地打包
- 安装包上传腾讯云 COS
- 根目录 `version.json` 作为云端远程配置文件
- 当前无 Windows / macOS 代码签名

## A. 发布前代码检查

- [ ] 当前工作区可以正常启动应用
- [ ] [frontend/js/app.js](/Users/chen/Code/MicroFlow/frontend/js/app.js) 通过语法检查
- [ ] Python 核心文件通过 `py_compile`
- [ ] 自动化测试通过
- [ ] 根目录 [version.json](/Users/chen/Code/MicroFlow/version.json) 已更新为本次发布版本
- [ ] [frontend/icons/icon.png](/Users/chen/Code/MicroFlow/frontend/icons/icon.png) 为本次正式应用图标

推荐命令：

```bash
cd /Users/chen/Code/MicroFlow

source .venv/bin/activate
node --check frontend/js/app.js
python -m py_compile main.py src/api.py src/database.py src/core/scheduler.py src/services/config_service.py src/services/telemetry_service.py src/llm_service.py
pytest -q
deactivate
```

## B. 生成发布图标

- [ ] 生成 `icon.ico`
- [ ] 生成 `icon.icns`
- [ ] 确认 macOS 托盘图标仍使用白色 `icon_white.png`

命令：

```bash
python3 scripts/build_release_icons.py
```

## C. macOS 打包

- [ ] 新建干净打包虚拟环境 `.venv-pack-macos`
- [ ] 安装 [requirements-packaging.txt](/Users/chen/Code/MicroFlow/requirements-packaging.txt)
- [ ] 确认测试机已安装 Chrome / Edge / Chromium 之一
- [ ] 成功生成 `dist/MicroFlow.app`
- [ ] 成功生成 `release/macos/MicroFlow-v1.0.0-macos-arm64.dmg`
- [ ] 成功生成备用 `zip`

核心命令：

```bash
cd /Users/chen/Code/MicroFlow

# 1. 创建干净的打包虚拟环境
rm -rf .venv-pack-macos
python3 -m venv .venv-pack-macos
source .venv-pack-macos/bin/activate

# 2. 安装打包依赖
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-packaging.txt

# 3. 生成发布图标
python3 scripts/build_release_icons.py

# 4. 清理旧构建文件
rm -rf build dist release/macos

# 5. 执行 PyInstaller 打包
PYINSTALLER_CONFIG_DIR=/Users/chen/Code/MicroFlow/.pyinstaller-cache python -m PyInstaller --clean --noconfirm MicroFlow.spec

# 6. 创建发布目录并生成 DMG
mkdir -p release/macos
./scripts/package_macos_dmg.sh dist/MicroFlow.app v1.0.0 arm64

# 7. 查看生成的文件和 SHA256
ls -lh release/macos/
shasum -a 256 release/macos/MicroFlow-v1.0.0-macos-arm64.dmg
```

## D. Windows 打包

- [ ] 在 Windows 机器上新建干净打包环境 `.venv-pack-win`
- [ ] 安装 [requirements-packaging.txt](/Users/chen/Code/MicroFlow/requirements-packaging.txt)
- [ ] 已安装 Python `3.12.x` 或 `3.11.x`，`3.13` 仅作备选，不要使用 Python `3.14`
- [ ] 确认测试机已安装 Microsoft Edge / Chrome / Chromium 之一
- [ ] 成功生成 `dist\\MicroFlow`
- [ ] 成功通过 Inno Setup 生成 `release\\windows\\MicroFlow-Setup-v1.0.0.exe`

说明：当前 Windows 打包基线为 Python 3.11；如果本机没有 3.11，请先安装后再执行下面命令。

核心命令：

```powershell
# 1. 进入项目目录
cd C:\AmazingSyncthing\Code\MicroFlow

# 2. 创建干净的打包虚拟环境
if (Test-Path .venv-pack-win) { Remove-Item .venv-pack-win -Recurse -Force }
py -3.11 -m venv .venv-pack-win

# 3. 安装打包依赖
.\.venv-pack-win\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-pack-win\Scripts\python.exe -m pip install -r requirements-packaging.txt

# 4. 生成发布图标
.\.venv-pack-win\Scripts\python.exe scripts\build_release_icons.py

# 5. 清理旧构建文件
if (Test-Path build) { Remove-Item build -Recurse -Force }
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path release\windows) { Remove-Item release\windows -Recurse -Force }

# 6. 执行 PyInstaller 打包
$env:PYINSTALLER_CONFIG_DIR="$PWD\.pyinstaller-cache"
.\.venv-pack-win\Scripts\python.exe -m PyInstaller --clean --noconfirm MicroFlow.windows.spec

# 7. 使用 Inno Setup 生成安装包
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
  "packaging\windows\MicroFlow.iss" `
  "/DMyAppVersion=v1.0.0" `
  "/DMySourceDir=$PWD\dist\MicroFlow" `
  "/DMyOutputDir=$PWD\release\windows"

# 8. 查看生成的文件和 SHA256
Get-ChildItem .\release\windows\ | Format-Table Name, Length
Get-FileHash .\release\windows\MicroFlow-Setup-v1.0.0.exe -Algorithm SHA256
```

## E. 计算安装包信息

- [ ] 计算 macOS `dmg` 的 `sha256`
- [ ] 记录 macOS `dmg` 文件大小
- [ ] 计算 Windows `exe` 的 `sha256`
- [ ] 记录 Windows `exe` 文件大小

macOS：

```bash
shasum -a 256 release/macos/MicroFlow-v1.0.0-macos-arm64.dmg
stat -f%z release/macos/MicroFlow-v1.0.0-macos-arm64.dmg
```

Windows：

```powershell
Get-FileHash .\release\windows\MicroFlow-Setup-v1.0.0.exe -Algorithm SHA256
(Get-Item .\release\windows\MicroFlow-Setup-v1.0.0.exe).Length
```

## F. 上传腾讯云 COS

- [ ] 上传 Windows 安装包到 COS
- [ ] 上传 macOS 安装包到 COS
- [ ] 上传最新 [version.json](/Users/chen/Code/MicroFlow/version.json) 到 COS 根目录
- [ ] 记录两个安装包的公网 URL

建议对象路径：

- `releases/v1.0.0/MicroFlow-Setup-v1.0.0.exe`
- `releases/v1.0.0/MicroFlow-v1.0.0-macos-arm64.dmg`
- `version.json`

## G. 回填云端版本信息

- [ ] 更新 [version.json](/Users/chen/Code/MicroFlow/version.json) 中的 `release_date`
- [ ] 更新 `downloads.windows.url`
- [ ] 更新 `downloads.windows.sha256`
- [ ] 更新 `downloads.windows.size`
- [ ] 更新 `downloads.macos.url`
- [ ] 更新 `downloads.macos.sha256`
- [ ] 更新 `downloads.macos.size`
- [ ] 检查公告正文中的下载链接是否与 COS 地址一致
- [ ] 检查 `telemetry.endpoint` 是否符合本次发布策略

## H. 发布后冒烟测试

- [ ] macOS 本机安装 `dmg` 后可正常启动
- [ ] macOS 首次启动时“右键打开”流程可用
- [ ] macOS 托盘、通知、抓取、详情页、截图、附件下载正常
- [ ] Windows 新机器安装 `exe` 后可正常启动
- [ ] Windows SmartScreen 提示后可继续运行
- [ ] Windows 托盘、通知、抓取、详情页、截图、附件下载正常
- [ ] 应用内“检查更新”能正确读取云端 `version.json`

## I. 本次发布注意事项

- [ ] 当前无代码签名，必须在安装说明中保留放行步骤
- [ ] 当前主版本仍统一为 `v1.0.0`，如需让客户端识别为新版本，必须递增 `version`，不能只改 `build`
- [ ] 打包时必须使用干净的专用虚拟环境，不要直接拿开发 `.venv` 生成正式包
- [ ] 如果 Windows 包体异常膨胀，优先检查是否误用了开发 `.venv`，或是否把测试/云服务/可选 provider 依赖带入
