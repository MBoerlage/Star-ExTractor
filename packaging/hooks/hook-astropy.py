"""Project hook for astropy (overrides the contrib hook, which imports every submodule, including
astropy.visualization that needs matplotlib). The app only reads and writes FITS."""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _keep(name: str) -> bool:
    return ".tests" not in name and not name.endswith(".conftest")


hiddenimports = collect_submodules("astropy.io.fits", filter=_keep) + collect_submodules("astropy.utils", filter=_keep)
for _pkg in ("astropy.units", "astropy.constants", "astropy.config", "astropy.extern"):  # imported by name at runtime
    hiddenimports += collect_submodules(_pkg, filter=_keep)
hiddenimports += ["astropy.logger"]
datas = collect_data_files("astropy", excludes=["**/tests/**", "**/*.c", "**/*.pyx", "**/*.h", "**/*.pyd", "**/*.so",
                                               "visualization/**", "wcs/**", "modeling/**", "cosmology/**"])
# the unit parsers read their generated ply tables as files at runtime
datas += collect_data_files("astropy", include_py_files=True, includes=["**/*parsetab.py", "**/*lextab.py"])
