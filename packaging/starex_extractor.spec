# PyInstaller spec: one-folder, windowed build -> dist/StarExtractor/StarExtractor.exe
# Build with packaging/build.ps1 (or: pyinstaller --noconfirm packaging/starex_extractor.spec).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is set by PyInstaller
PKG = ROOT / "starex_reduce"

# Every bundled data file, listed explicitly (a missing data file is the classic packaging bug).
datas = [
    (str(PKG / "reference_spectra"), "starex_reduce/reference_spectra"),  # Pickles (1998) library
    (str(PKG / "data"), "starex_reduce/data"),  # reference stars, telluric lines, extinction, camera table
    (str(PKG / "resources"), "starex_reduce/resources"),  # logo SVGs and starex.ico
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
exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="StarExtractor", console=False, upx=False,
    icon=str(PKG / "resources" / "starex.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="StarExtractor", upx=False)  # noqa: F821
