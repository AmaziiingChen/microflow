# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.datastruct import TOC
from PyInstaller.utils.hooks import collect_all

block_cipher = None

excluded_modules = [
    'pandas', 'matplotlib', 'streamlit',
    'scipy', 'sympy', 'notebook', 'PyQt5', 'PySide6', 'tkinter',
    'Cocoa', 'AppKit', 'Foundation', 'PyObjCTools', 'objc',
    'webview.platforms.cocoa',
    'pytest', '_pytest', 'py', 'IPython',
    'sqlalchemy', 'fsspec',
    'rich',
    'tensorflow', 'torch'
]

hidden_modules = [
    'pystray',
    'pystray._win32',
    'plyer.platforms.win.notification',
    'webview.platforms.edgechromium',
    'webview.platforms.winforms',
    'truststore',
    'mistune',
    'psutil',
    'psutil._pswindows'
]

ai_runtime_packages = [
    'numpy',
    'openai',
    'playwright',
    'undetected_playwright',
    'tiktoken',
    'ollama',
    'langchain',
    'langchain_classic',
    'langchain_core',
    'langchain_openai',
    'langchain_community',
    'langchain_aws',
    'langchain_mistralai',
    'langchain_ollama',
    'scrapegraphai',
    'scrapegraph_py',
    'trafilatura',
    'courlan',
    'htmldate',
    'justext',
    'dateparser',
    'babel',
    'duckduckgo_search',
    'free_proxy',
    'html2text',
    'minify_html',
    'async_timeout',
    'semchunk',
    'boto3',
    'botocore',
    's3transfer',
    'jmespath',
    'primp',
    'tld',
    'mpire',
]

ai_datas = []
ai_binaries = []
ai_hiddenimports = []

for package_name in ai_runtime_packages:
    datas, binaries, hiddenimports = collect_all(package_name)
    ai_datas += datas
    ai_binaries += binaries
    ai_hiddenimports += hiddenimports

hidden_modules = sorted(set(hidden_modules + ai_hiddenimports))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('frontend', 'frontend'),
        ('data', 'data'),
        ('src/services/snapshot_template.html', 'src/services'),
    ] + ai_datas,
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

for dest_name, src_name, typecode in TOC(list(a.binaries) + ai_binaries):
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
