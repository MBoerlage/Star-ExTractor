# PyInstaller spec: one-folder, windowed build -> dist/StarExtractor/StarExtractor.exe
# Build with packaging/build.ps1 (or: pyinstaller --noconfirm packaging/starex_extractor.spec).
# Bootloader splash (stage 1, docs/PACKAGING.md): on by default; STAREX_BOOT_SPLASH=0 (build.ps1 -NoBootSplash)
# leaves it out. It needs Tcl/Tk in the building Python; without it the build goes on without it and says so.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is set by PyInstaller
PKG = ROOT / "starex_reduce"

# Every bundled data file, listed explicitly (a missing data file is the classic packaging bug).
datas = [
    (str(PKG / "reference_spectra"), "starex_reduce/reference_spectra"),  # Pickles (1998) library
    (str(PKG / "data"), "starex_reduce/data"),  # reference stars, telluric lines, extinction, camera table
    (str(PKG / "resources"), "starex_reduce/resources"),  # logo SVGs, starex.ico, splash.png + splash@2x.png
    (str(PKG / "ui" / "icons"), "starex_reduce/ui/icons"),  # theme icons
    (str(PKG / "ui" / "about"), "starex_reduce/ui/about"),  # About splash
]
datas += collect_data_files("astropy_iers_data")  # astropy never needs to download at first run
datas += copy_metadata("starex-reduce")  # the version comes from pyproject.toml through the metadata

# Qt and Python modules the app never uses: keeps the folder small.
excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "pytestqt", "_pytest", "ruff",
    "PyInstaller", "setuptools", "pip", "pandas", "PIL", "sympy", "numba",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtNetworkAuth",
    "PySide6.QtUiTools", "PySide6.QtAxContainer", "PySide6.QtConcurrent", "PySide6.QtDBus",
    "pyqtgraph.opengl", "pyqtgraph.examples",  # keep PySide6.QtOpenGL: pyqtgraph imports it for every plot
    "pyqtgraph.jupyter", "numpy.tests", "scipy.tests",  # astropy.tests: astropy imports it at start-up
    "astropy.visualization", "astropy.modeling", "astropy.cosmology", "astropy.convolution", "astropy.samp",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # the screens are imported by name when first opened (main_window.LAZY_SCREENS): collect them all
    hiddenimports=["PySide6.QtSvg", "astropy_iers_data"] + collect_submodules("starex_reduce"),
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

# Stage 1: the PyInstaller bootloader splash, shown before Python starts. The same 1x image as the Qt splash
# (stage 2, starex_reduce/ui/splash.py), on the same screen and at the same size (the exe is per-monitor DPI aware
# through its manifest), so the hand-off is seamless at 100-149 % display scaling (from 150 % the Qt splash uses
# the 2x image and is drawn twice as large in device pixels as stage 1).
# app.py closes it (pyi_splash.close) once the Qt splash is on screen.
splash = None
if os.environ.get("STAREX_BOOT_SPLASH", "1") != "0":
    try:
        splash = Splash(  # noqa: F821
            str(PKG / "resources" / "splash.png"), binaries=a.binaries, datas=a.datas,
            text_pos=None, always_on_top=True, minify_script=True,
            center="active",  # the screen with the mouse, as the Qt splash: both appear in the same place
            max_img_size=(800, 448),  # the 1x image as it is (PyInstaller's default limit is 760 x 480)
        )
    except (SystemExit, Exception) as e:  # noqa: BLE001 - no Tcl/Tk here: build without stage 1, never fail
        print(f"WARNING: bootloader splash left out ({e}). The Qt splash still shows; see docs/PACKAGING.md.")
        splash = None
else:
    print("NOTE: bootloader splash turned off (STAREX_BOOT_SPLASH=0)")

exe = EXE(  # noqa: F821
    pyz, a.scripts, *([splash] if splash else []), [], exclude_binaries=True, name="StarExtractor", console=False,
    upx=False, icon=str(PKG / "resources" / "starex.ico"),
    manifest=str(ROOT / "packaging" / "StarExtractor.manifest"),  # per-monitor DPI aware from the first instruction
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas, *([splash.binaries] if splash else []), name="StarExtractor", upx=False,
)
