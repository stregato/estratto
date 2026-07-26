from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path.cwd()
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("telethon")
    + collect_submodules("ebooklib")
)

a = Analysis(
    [str(ROOT / "estratto" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "config.yaml"), "."),
        (str(ROOT / "web" / "static"), "web/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Estratto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Estratto",
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Estratto.app",
        icon=None,
        bundle_identifier="com.doublecorretto.estratto",
    )
