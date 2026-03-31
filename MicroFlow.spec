# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# 1. 精准排除无关的重型库（保持体积小巧）
excluded_modules = [
    'pandas', 'numpy', 'matplotlib', 'streamlit', 
    'scipy', 'sympy', 'notebook', 'PyQt5', 'PySide6', 'tkinter'
]

# 2. 补全动态加载的隐藏依赖
# plyer 和 pywebview 在运行时会动态调用系统底层接口，PyInstaller 常常无法自动侦测到
hidden_modules = [
    'plyer.platforms.macosx.notification',  # macOS 桌面通知必须项
    'webview.platforms.cocoa',              # pywebview macOS 渲染后端
    'truststore',                           # 强制引入 SSL 信任库
    'mistune'
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # 如果你已经清理了字体，这里的 frontend 和 data 文件夹体积应该很小了
    datas=[('frontend', 'frontend'), ('data', 'data')],
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
    argv_emulation=True,   # 允许 macOS 将文件拖拽到图标上打开
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
    icon='frontend/icons/icon_white.png', # 推荐换成 .icns 格式体验更好
    bundle_identifier='com.microflow.app', # 替换为你自己的标识符
    info_plist={
        'NSHighResolutionCapable': 'True', # 开启 Retina 屏幕高清渲染
        'LSUIElement': 'False',            # 如果你只想做状态栏菜单，设为 True 会隐藏 Dock 图标
    },
)