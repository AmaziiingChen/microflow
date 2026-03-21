# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # 注意：如果你用了方案 A 删除了字体，这里的 frontend 依然保留，因为它会打包 HTML/JS/CSS 等必要文件
    datas=[('frontend', 'frontend'), ('data/config.json', 'data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 🌟 核心：强制排除掉可能被第三方库间接引入的重型包
    # 🌟 核心防御：强制排除所有重型数据/Web/AI框架
    excludes=[
        'pandas', 'numpy', 'matplotlib', 'streamlit', 'playwright', 
        'pyarrow', 'langchain', 'boto3', 'botocore', 'sqlalchemy', 
        'altair', 'scipy', 'PyQt5', 'PySide6', 'tkinter', 'notebook',
        'scrapegraph', 'trafilatura', 'tiktoken', 'huggingface_hub', 'httpx'
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MicroFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,  # 剥离符号表，进一步减小体积
    upx=True,    # 🌟 核心：启用 UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # 设置为 False 以隐藏命令行黑框
    disable_windowed_traceback=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
    icon='frontend/icons/icon_white.png'
)