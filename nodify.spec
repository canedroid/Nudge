# PyInstaller specification for Nodify.
#
# Built with:
#     .venv\Scripts\python -m PyInstaller nodify.spec --noconfirm
#
# Two decisions are worth stating, because both were the result of the application
# not working otherwise.
#
# The window is built as a windowed application, with no console. A console window
# behind a fullscreen always-on-top overlay is not merely ugly: it is a second
# always-on-top window competing for attention with the product itself.
#
# Qt is excluded from byte-compilation. The plugins are large compiled objects
# that PyInstaller's bytecode step mangles, producing an application that starts
# and then dies when a plugin is loaded.

import sys
from pathlib import Path

# PyInstaller wants forward slashes in the pathex, even on Windows.
project_root = Path(SPECPATH).resolve()
src_dir = project_root / "src"

block_cipher = None

a = Analysis(
    ["src/nodify/__main__.py"],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[],
    # Qt's compiled plugins break PyInstaller's bytecode step.
    excludes=[
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineQuick",
        "PyQt6.QtMultimedia",
        "PyQt6.Qt3DCore",
        "PyQt6.QtBluetooth",
        "PyQt6.QtQuick3D",
        "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization",
        "PyQt6.QtDesigner",
        "PyQt6.QtHelp",
        "PyQt6.QtLocation",
        "PyQt6.QtNetworkAuth",
        "PyQt6.QtNfc",
        "PyQt6.QtPdf",
        "PyQt6.QtPositioning",
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
        "PyQt6.QtRemoteObjects",
        "PyQt6.QtScxml",
        "PyQt6.QtSensors",
        "PyQt6.QtSerialPort",
        "PyQt6.QtSpatialAudio",
        "PyQt6.QtSql",
        "PyQt6.QtStateMachine",
        "PyQt6.QtTest",
        "PyQt6.QtTextToSpeech",
        "PyQt6.QtWebChannel",
        "PyQt6.QtWebSockets",
        "tkinter",
        "unittest",
        "pydoc_data",
        "numpy",
        "PIL",
        "pytest",
        "mypy",
        "ruff",
    ],
    # The reference kit is not used by the application; it is kept as source only.
    hiddenimports=["nodify.ui.kit"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # No console: the overlay is the product, and a console window behind it is a
    # second always-on-top window competing for attention.
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Nodify",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # One folder rather than one file. A single-file build unpacks to a temporary
    # directory on every launch, which is slow and breaks atomic replacement when
    # the vault is on a different volume.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    # UAC is not required: the application writes only to the user's own config
    # directory and the vault the user chose.
    uac_admin=False,
)
