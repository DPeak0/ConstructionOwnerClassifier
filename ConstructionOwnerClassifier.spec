# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


datas = collect_data_files("rapidocr_onnxruntime") + [("assets/app.ico", "assets")]
hiddenimports = collect_submodules("rapidocr_onnxruntime")

a = Analysis(
    ["main.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5", "tkinter", "matplotlib", "pandas", "lxml", "cryptography",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtVirtualKeyboard",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "onnxruntime.tools", "onnxruntime.transformers", "onnxruntime.quantization",
    ],
    noarchive=False,
    optimize=1,
)

def keep_binary(entry):
    destination = entry[0].replace("\\", "/").lower()
    if "opencv_videoio_ffmpeg" in destination:
        return False
    if "/translations/" in destination:
        return False
    if "/plugins/imageformats/" in destination and not any(
        name in destination for name in ("qjpeg", "qwebp", "qico")
    ):
        return False
    return True

a.binaries = [entry for entry in a.binaries if keep_binary(entry)]
a.datas = [entry for entry in a.datas if "/translations/" not in entry[0].replace("\\", "/").lower()]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ConstructionOwnerClassifier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/app.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ConstructionOwnerClassifier",
)
