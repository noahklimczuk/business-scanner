# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

    pyinstaller build/leadsmith.spec --noconfirm

Produces dist/Leadsmith.exe — one file, no installer, no Python on the machine.
Run it from a folder it can write to: the database, config.json and the
generated sites all land beside the executable (see leadsmith_app.py), which is
what makes the whole thing copyable to another machine on a memory stick.

The excludes matter. PySide6 ships a very large surface — QML, WebEngine, 3D,
multimedia, charts — and none of it is used here. Leaving them in roughly
triples the download for no behaviour.
"""
import os

ROOT = os.path.abspath(os.path.join(os.getcwd()))

# Jinja templates are read from disk at runtime, so they have to travel.
datas = [(os.path.join(ROOT, "templates"), "templates")]

# The Pages Function deployed alongside a launched site. It arrives with
# Phase 5; PyInstaller raises on a data path that does not exist, so a spec
# that names it unconditionally cannot build a checkout without it.
_functions = os.path.join(ROOT, "functions")
if os.path.isdir(_functions):
    datas.append((_functions, "functions"))

hiddenimports = [
    # Imported lazily or by name, so PyInstaller's static analysis misses them.
    "segno", "phonenumbers", "anthropic", "jinja2", "markupsafe",
]

excludes = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSpatialAudio",
    # The CLI's terminal stack. The windowed build has no terminal.
    "tkinter", "test", "pytest", "IPython", "matplotlib", "numpy", "PIL",
]

a = Analysis(
    [os.path.join(ROOT, "leadsmith_app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

# Shown by the bootloader, in C, before a line of our Python runs. That is the
# only thing that can cover the wait: unpacking ~56MB to a temp directory and
# then importing PySide6 accounts for most of the startup, and a Qt splash
# cannot appear during the import of Qt itself. `startup.done()` takes it down
# once the window is on screen.
splash = Splash(
    os.path.join(ROOT, "build", "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(40, 196),
    text_size=9,
    text_color="#9c9ca6",            # theme.DARK.muted
    text_default="Starting…",
    # A loading box that sits above every other window while someone works is
    # a nuisance; it is already the thing they just double-clicked.
    always_on_top=False,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="Leadsmith",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window. A GUI app that flashes a black terminal on launch
    # looks broken to the person it is built for.
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "build", "leadsmith.ico"),
)
