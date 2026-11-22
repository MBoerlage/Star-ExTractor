"""The one line catalogue (no Qt): sky, telluric, interstellar and stellar lines used by the target calibration
(core/targetcal.py), the sky-line detector (core/skylines.py) and, later, the line-marker chooser.

Air wavelengths in Å, consistent with the Balmer table of core/wavelength.py (BALMER is used as is). Sources: NIST
Atomic Spectra Database (air values) for the atomic lines; Hobbs et al. 2008 (ApJ 680, 1256) for the diffuse
interstellar bands; telluric band positions are the band heads / centres used for differential matching only (they
are never treated as exact lines). docs/WAVELENGTH.md lists which values were checked and how.
"""
from __future__ import annotations

from dataclasses import dataclass

from .wavelength import BALMER

SKY_AIRGLOW, SKY_LAMP, TELLURIC, ISM, STELLAR_BALMER, STELLAR_HE = (
    "sky_airglow", "sky_lamp", "telluric", "ism", "stellar_balmer", "stellar_he")
SKY_KINDS = (SKY_AIRGLOW, SKY_LAMP)
FIXED_KINDS = (SKY_AIRGLOW, SKY_LAMP, TELLURIC, ISM)  # zero velocity: they pin the instrument
STELLAR_KINDS = (STELLAR_BALMER, STELLAR_HE)


@dataclass(frozen=True)
class Line:
    label: str
    lam_a: float  # air, Å (a blend: its effective centre at low resolution)
    kind: str
    group: str  # ion or molecule
    blend: bool = False
    weight: float = 1.0
    fwhm_a: float = 0.0  # typical width hint at R ~ 1000 (0 = instrumental)
    note: str = ""
    span: tuple[float, float] | None = None  # telluric bands: the window used for matching (Å)


LINES: tuple[Line, ...] = (
    # ---- night sky: airglow and street lamps -----------------------------------------------------------------
    Line("Hg I 4047", 4046.563, SKY_LAMP, "Hg I", note="mercury street lamps"),
    Line("Hg I 4358", 4358.328, SKY_LAMP, "Hg I", note="mercury street lamps"),
    Line("Hg I 5461", 5460.735, SKY_LAMP, "Hg I", note="mercury street lamps; often the strongest sky line"),
    Line("Hg I 5770", 5769.598, SKY_LAMP, "Hg I", weight=0.7, note="mercury yellow doublet, blue member"),
    Line("Hg I 5791", 5790.663, SKY_LAMP, "Hg I", weight=0.7, note="mercury yellow doublet, red member"),
    Line("O I 5577", 5577.339, SKY_AIRGLOW, "O I", note="green airglow line"),
    Line("[O I] 6300", 6300.304, SKY_AIRGLOW, "[O I]", note="red airglow line"),
    Line("[O I] 6364", 6363.776, SKY_AIRGLOW, "[O I]", weight=0.6, note="red airglow line, a third of 6300"),
    Line("Na D sky", 5892.94, SKY_LAMP, "Na I", blend=True, weight=0.4, fwhm_a=6.0,
         note="Na D 5889.95 + 5895.92 (airglow and low-pressure sodium); one blend at low resolution"),
    Line("Na 5685 (HPS)", 5685.4, SKY_LAMP, "Na I", blend=True, weight=0.3, fwhm_a=12.0,
         note="high-pressure sodium lamps: Na I 5682.63 + 5688.21, pressure-broadened; wide, low weight"),
    Line("Na 6157 (HPS)", 6157.5, SKY_LAMP, "Na I", blend=True, weight=0.3, fwhm_a=12.0,
         note="high-pressure sodium lamps: Na I 6154.23 + 6160.75; wide, low weight"),
    # ---- telluric bands (differential: matched against the reference star, never as exact lines) --------------
    Line("O2 B", 6867.2, TELLURIC, "O2", note="band head", span=(6840.0, 6960.0)),
    Line("H2O 7186", 7186.0, TELLURIC, "H2O", weight=0.5, note="band centre; strength varies with humidity",
         span=(7150.0, 7330.0)),
    Line("O2 A", 7593.7, TELLURIC, "O2", note="band head", span=(7580.0, 7720.0)),
    Line("O2 γ", 6277.7, TELLURIC, "O2", weight=0.3, note="weak band", span=(6270.0, 6310.0)),
    # ---- interstellar ----------------------------------------------------------------------------------------
    Line("Na D1 ISM", 5895.924, ISM, "Na I", weight=0.5, note="interstellar Na D1"),
    Line("Na D2 ISM", 5889.950, ISM, "Na I", weight=0.5, note="interstellar Na D2"),
    Line("DIB 5780", 5780.48, ISM, "DIB", weight=0.5, fwhm_a=2.1, note="Hobbs et al. 2008"),
    Line("DIB 5797", 5797.06, ISM, "DIB", weight=0.4, fwhm_a=1.0, note="Hobbs et al. 2008"),
    Line("DIB 6284", 6283.84, ISM, "DIB", weight=0.3, fwhm_a=4.5, note="Hobbs et al. 2008; inside O2 γ"),
    Line("DIB 6614", 6613.62, ISM, "DIB", weight=0.4, fwhm_a=1.0, note="Hobbs et al. 2008"),
    Line("Ca II K ISM", 3933.663, ISM, "Ca II", weight=0.2, note="interstellar Ca II K, at the blue edge"),
    # ---- stellar ---------------------------------------------------------------------------------------------
    *(Line(name, lam, STELLAR_BALMER, "H I", fwhm_a=20.0) for name, lam in BALMER.items()),
    Line("He I 4026", 4026.19, STELLAR_HE, "He I"),
    Line("He I 4388", 4387.93, STELLAR_HE, "He I"),
    Line("He I 4471", 4471.48, STELLAR_HE, "He I"),
    Line("He I 4713", 4713.14, STELLAR_HE, "He I", weight=0.7),
    Line("He I 4922", 4921.93, STELLAR_HE, "He I"),
    Line("He I 5016", 5015.68, STELLAR_HE, "He I", weight=0.7),
    Line("He I 5876", 5875.62, STELLAR_HE, "He I"),
    Line("He I 6678", 6678.15, STELLAR_HE, "He I"),
    Line("He I 7065", 7065.19, STELLAR_HE, "He I"),
    Line("He II 4200", 4199.83, STELLAR_HE, "He II", weight=0.5),
    Line("He II 4541", 4541.59, STELLAR_HE, "He II", weight=0.5),
    Line("He II 4686", 4685.71, STELLAR_HE, "He II", weight=0.7),
    Line("He II 5412", 5411.52, STELLAR_HE, "He II", weight=0.5),
)
BY_LABEL = {ln.label: ln for ln in LINES}


def of_kind(*kinds: str) -> list[Line]:
    return [ln for ln in LINES if ln.kind in kinds]


def sky_lines() -> list[Line]:
    return of_kind(*SKY_KINDS)
