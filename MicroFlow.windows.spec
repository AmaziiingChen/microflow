# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.datastruct import TOC

block_cipher = None

excluded_modules = [
    'pandas', 'numpy', 'matplotlib', 'streamlit',
    'scipy', 'sympy', 'notebook', 'PyQt5', 'PySide6', 'tkinter',
    'Cocoa', 'AppKit', 'Foundation', 'PyObjCTools', 'objc',
    'webview.platforms.cocoa',
    'pytest', '_pytest', 'py', 'IPython',
    'boto3', 'botocore', 's3transfer',
    'sqlalchemy', 'fsspec',
    'langchain_aws', 'langchain_community',
    'langchain_mistralai', 'langchain_ollama',
    'rich', 'babel', 'dateparser',
    'tensorflow', 'torch'
]

hidden_modules = [
    'pystray',
    'pystray._win32',
    'plyer.platforms.win.notification',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'truststore',
    'mistune'
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MicroFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='frontend/icons/icon.ico',
)

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
