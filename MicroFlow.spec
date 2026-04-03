# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

try:
    webview_hidden_modules = ['webview'] + collect_submodules('webview')
    webview_data_files = collect_data_files('webview')
except Exception as exc:
    raise SystemExit(
        "缺少 pywebview/webview 打包依赖。请先激活项目虚拟环境后再执行 PyInstaller，"
        "例如：`source .venv/bin/activate && python -m PyInstaller --clean --noconfirm MicroFlow.spec`"
    ) from exc

# 1. 精准排除无关的重型库（保持体积小巧）
excluded_modules = [
    'pandas', 'numpy', 'matplotlib', 'streamlit',
    'scipy', 'sympy', 'notebook', 'PyQt5', 'PySide6', 'tkinter',
    'pystray', 'pystray._win32', 'webview.platforms.winforms',
    'webview.platforms.edgechromium', 'clr',
    'pytest', '_pytest', 'py', 'IPython',
    'boto3', 'botocore', 's3transfer',
    'sqlalchemy', 'fsspec',
    'langchain_aws', 'langchain_community',
    'langchain_mistralai', 'langchain_ollama',
    'rich', 'babel', 'dateparser',
    'tensorflow', 'torch'
]

# 2. 补全动态加载的隐藏依赖
# plyer 和 pywebview 在运行时会动态调用系统底层接口，PyInstaller 常常无法自动侦测到
hidden_modules = [
    'plyer.platforms.macosx.notification',  # macOS 桌面通知必须项
    'webview.platforms.cocoa',              # pywebview macOS 渲染后端
    'truststore',                           # 强制引入 SSL 信任库
    'mistune'
] + webview_hidden_modules

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # 如果你已经清理了字体，这里的 frontend 和 data 文件夹体积应该很小了
    datas=[
        ('frontend', 'frontend'),
        ('data', 'data'),
        ('src/services/snapshot_template.html', 'src/services'),
    ] + webview_data_files,
    hiddenimports=hidden_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Playwright 下载的浏览器运行时体积极大，且在 macOS 下会触发复杂的嵌套签名问题。
# 发布包统一改为调用用户本机的 Edge / Chrome / Chromium，因此这里直接从 bundle 中排除。
PLAYWRIGHT_BROWSER_MARKER = "playwright/driver/package/.local-browsers/"
EXCLUDED_DATA_MARKERS = (
    PLAYWRIGHT_BROWSER_MARKER,
    "frontend/icons/.DS_Store",
    "frontend/icons/1034.png",
    "frontend/icons/1034.ico",
    "frontend/icons/1034.icns",
    "frontend/icons/icon.ico",
    "frontend/icons/icon.icns",
    "frontend/fonts/custom_font.ttf",
)
filtered_binaries = []
filtered_datas = []

for dest_name, src_name, typecode in a.binaries:
    normalized_target = f"{dest_name}::{src_name}".replace("\\", "/")
    if PLAYWRIGHT_BROWSER_MARKER not in normalized_target:
        filtered_binaries.append((dest_name, src_name, typecode))

a.binaries = TOC(filtered_binaries)
for dest_name, src_name, typecode in a.datas:
    normalized_target = f"{dest_name}::{src_name}".replace("\\", "/")
    if not any(marker in normalized_target for marker in EXCLUDED_DATA_MARKERS):
        filtered_datas.append((dest_name, src_name, typecode))

a.datas = TOC(filtered_datas)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 3. 构建基础可执行文件
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MicroFlow_exec', # 内部执行文件名
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # 坚决不开启 UPX，保护 macOS 签名
    console=False,         # 关闭终端黑框
    disable_windowed_traceback=False,
    argv_emulation=False,  # 关闭 argv emulation，避免 macOS GUI 启动阶段异常中止
    target_arch='arm64',   # 直接指定为 Apple Silicon 原生架构，运行极其流畅
    codesign_identity=None,
    entitlements_file=None,
)

# 4. 收集依赖文件放入一个目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MicroFlow',
)

# 5. 构建 macOS 专属的 .app 应用程序包
# 这一步对于 GUI 应用至关重要，否则程序无法获取正确的系统焦点和 Dock 图标
app = BUNDLE(
    coll,
    name='MicroFlow.app',
    icon='frontend/icons/icon.icns',
    bundle_identifier='com.microflow.app', # 替换为你自己的标识符
    info_plist={
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,  # 开启 Retina 屏幕高清渲染
        'LSUIElement': False,             # 保留 Dock 图标与正常应用形态
    },
)
