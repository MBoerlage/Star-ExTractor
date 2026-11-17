"""Export (no Qt): AAVSO AVSpec FITS header, pre-flight checks, file name, .dat/CSV.

Keywords follow the AAVSO manual v1.4 (docs/FORMATS.md): the AAV_* names AVSpec reads first. BeSS/ARAS
keywords are not written (ARAS export was removed; spectra can be mailed to ARAS).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from starex_reduce import APP_NAME, __version__

from .airmass import julian_date, mean_airmass
from .coords import parse_dec, parse_ra
from .fitsio import STRUCTURAL, read_header_info, write_float32
from .frames import parse_time

DEFAULT_RESOLVING_POWER = 500  # accepted by AAVSO for this setup (EXAMPLE_NOVA.md); a setting
ISOT = "%Y-%m-%dT%H:%M:%S.%f"
STAGE_FILES = ("{img}_S.fit", "{img}_G.fit", "{img}_X.fit", "{img}_W.fit", "{img}_R.fit")


@dataclass
class ObserverInfo:
    obscode: str = ""
    name: str = ""
    site_name: str = ""
    lat: float | None = None
    lon: float | None = None
    elev: float | None = None
    instrument: str = ""  # AAVSO equipment package nickname (AAV_INST)
    spectrograph: str = ""
    telescope: str = ""
    camera: str = ""
    resolving_power: int = DEFAULT_RESOLVING_POWER

    @classmethod
    def from_settings(cls, s: dict[str, Any]) -> ObserverInfo:
        obs, site, eq = s.get("observer") or {}, s.get("site") or {}, s.get("equipment") or {}
        return cls(obs.get("obscode", ""), obs.get("name", ""), site.get("name", ""), site.get("lat"),
                   site.get("lon"), site.get("elev"), eq.get("name", ""), eq.get("spectrograph", ""),
                   eq.get("telescope", ""), eq.get("camera", ""),
                   int(s.get("resolving_power") or DEFAULT_RESOLVING_POWER))

    def to_settings(self) -> dict[str, Any]:
        return {"observer": {"obscode": self.obscode, "name": self.name},
                "site": {"name": self.site_name, "lat": self.lat, "lon": self.lon, "elev": self.elev},
                "equipment": {"name": self.instrument, "spectrograph": self.spectrograph,
                              "telescope": self.telescope, "camera": self.camera},
                "resolving_power": self.resolving_power}


def isot(t: datetime) -> str:
    """ISOT with milliseconds, no time zone suffix (as AVSpec's example and BASS's file)."""
    return t.replace(tzinfo=None).strftime(ISOT)[:-3]


@dataclass
class Times:
    start: datetime
    end: datetime
    exptime: float  # total exposure (s)
    exptime2: str  # "6 x 300 s"

    @property
    def mid(self) -> datetime:
        return self.start + (self.end - self.start) / 2

    @property
    def jd_mid(self) -> float:
        return julian_date(self.mid)


def times_of(h: dict[str, Any]) -> Times | None:
    start, end = parse_time(h.get("DATE-OBS")), parse_time(h.get("DATE-END"))
    exp = h.get("EXPTIME")
    n = int(h.get("NCOMBINE", 1) or 1)
    if start is None:
        return None
    total = float(exp) * n if isinstance(exp, (int, float)) else None
    if end is None and total is not None:
        end = start + timedelta(seconds=total)
    if end is None:
        return None
    if total is None:
        total = (end - start).total_seconds()
    e2 = h.get("EXPTIME2") or (f"{n} x {float(exp):g} s" if isinstance(exp, (int, float)) else f"{total:g} s")
    return Times(start, end, round(total, 3), str(e2))


def history_chain(folder: Path, image: str = "Target") -> list[str]:
    """HISTORY of every stage file of this image, in processing order (documents the reduction)."""
    out: list[str] = []
    for pat in STAGE_FILES:
        p = folder / pat.format(img=image)
        if p.exists():
            for line in read_header_info(p).history:
                if line and line not in out:
                    out.append(line)
    return out


@dataclass
class ExportPlan:
    header: dict[str, Any]
    history: list[str]
    airmass: float | None
    times: Times | None
    notes: list[str] = field(default_factory=list)


def build_header(r_header: dict[str, Any], obs: ObserverInfo, objname: str, coords: dict[str, Any] | None,
                 folder: Path | None) -> ExportPlan:
    """The published header: AVSpec mandatory + recommended keys (AAV_* names); inherited BSS_* keys dropped."""
    h = {k: v for k, v in r_header.items() if k not in STRUCTURAL and not k.startswith("BSS_")}
    for k in ("DATE-AVG", "JD_UTC", "OBJECT", "BUNIT"):
        h.pop(k, None)
    t = times_of(r_header)
    notes: list[str] = []
    h["OBJNAME"] = objname
    h["OBJECT"] = objname  # alias of OBJNAME: keep both the same
    if t is not None:
        h["DATE-OBS"], h["DATE-END"] = isot(t.start), isot(t.end)
        h["EXPTIME"], h["EXPTIME2"], h["JD"] = t.exptime, t.exptime2, round(t.jd_mid, 6)
    h.update({"CTYPE1": "Wavelength", "CUNIT1": "Angstrom", "CRPIX1": 1})
    h["OBSERVER"] = obs.obscode
    if obs.site_name:
        h["AAV_SITE"] = obs.site_name
    if obs.instrument:
        h["AAV_INST"] = obs.instrument
    h["AAV_ITRP"] = int(obs.resolving_power)
    h["VERSION"] = f"{APP_NAME} {__version__}"
    for k in ("WAV_FWHM",):
        h.pop(k, None)
    airmass = None
    rd = None
    try:
        if coords and coords.get("ra") and coords.get("dec"):
            rd = parse_ra(coords["ra"]), parse_dec(coords["dec"])
    except ValueError:
        rd = None
    if isinstance(r_header.get("AIRMASS"), (int, float)):
        airmass = float(r_header["AIRMASS"])  # the value the Response step used: one airmass everywhere
    elif t is not None and rd is not None and obs.lat is not None and obs.lon is not None:
        airmass = mean_airmass(rd[0], rd[1], float(obs.lat), float(obs.lon), t.start, t.end)
        h["AIRMASS"] = round(airmass, 4)
    history = history_chain(folder) if folder is not None else []
    history.append(f"{APP_NAME} export: OBSERVER is the AAVSO obscode; no heliocentric correction; "
                   "telluric features kept")
    if obs.spectrograph or obs.telescope or obs.camera:
        history.append("equipment: " + ", ".join(x for x in (obs.telescope, obs.spectrograph, obs.camera) if x))
    if r_header.get("RSP_MODE") == "none":
        notes.append("No response applied: the file holds normalised counts (as allowed by AVSpec).")
    return ExportPlan(h, history, airmass, t, notes)


# ---- pre-flight ------------------------------------------------------------------------------------
@dataclass
class Check:
    state: str  # ok | warn | err
    text: str


def preflight_avspec(plan: ExportPlan, flux: np.ndarray, vsx: dict[str, Any] | None) -> list[Check]:
    h = plan.header
    out: list[Check] = []
    finite = np.isfinite(flux)
    out.append(Check("ok" if flux.ndim == 1 and finite.sum() > 10 else "err",
                     "1D, wavelength calibrated, data present" if finite.sum() > 10 else "The spectrum is empty"))
    out.append(Check("ok" if h.get("CUNIT1") == "Angstrom" else "err", "Wavelength unit: Angstrom (CUNIT1)"))
    out.append(Check("ok" if h.get("RSP_MODE") != "none" else "warn",
                     "Scaled: response-corrected and normalised" if h.get("RSP_MODE") != "none"
                     else "Not response-corrected (No response chosen): normalised counts"))
    t = plan.times
    out.append(Check("ok" if t else "err", "DATE-OBS, DATE-END, EXPTIME in ISOT; JD at mid-exposure" if t else
                     "Observation times missing (DATE-OBS/DATE-END)"))
    code = str(h.get("OBSERVER") or "")
    out.append(Check("ok" if re.fullmatch(r"[A-Z0-9]{2,6}", code) else "err",
                     f"OBSERVER = AAVSO obscode {code}" if code else "Enter your AAVSO obscode (Observer)"))
    name = h.get("OBJNAME") or ""
    if not name:
        out.append(Check("err", "Enter the object name (OBJNAME)"))
    elif vsx is not None and vsx.get("found") and (vsx.get("object") or {}).get("name", "").lower() == name.lower():
        out.append(Check("ok", f"OBJNAME {name}: found in VSX"))
    elif vsx is not None and not vsx.get("found") and not vsx.get("offline") and vsx.get("query") == name:
        out.append(Check("warn", f"OBJNAME {name}: {vsx.get('message', 'not found in VSX')}"))
    else:
        out.append(Check("off", f"OBJNAME {name} (VSX check optional; AVSpec needs a name VSX knows)"))
    for key, what in (("AAV_SITE", "site nickname"), ("AAV_INST", "equipment nickname")):
        out.append(Check("ok" if h.get(key) else "warn", f"{what.capitalize()}: {h[key]}" if h.get(key) else
                         f"Enter the {what} exactly as registered at AAVSO"))
    if plan.airmass is None:
        out.append(Check("warn", "Airmass not computed: needs the site latitude/longitude and the target coordinates"))
    else:
        ok = 1.0 <= plan.airmass <= 5.0
        out.append(Check("ok" if ok else "err", f"Airmass {plan.airmass:.2f} at mid-exposure" +
                         ("" if ok else " is outside 1–5: AVSpec will reject it")))
    out.append(Check("ok", "No velocity correction applied, telluric features kept"))
    out.append(Check("warn", "First submission? AAVSO first wants one spectrum of a standard star"))
    return out


def verify_file(path: Path, n: int) -> list[Check]:
    """Read a written export back with astropy and check what AVSpec reads from it."""
    from astropy.io import fits

    try:
        with fits.open(path) as hdul:
            h = hdul[0].header
            data = np.asarray(hdul[0].data)
    except (OSError, ValueError) as exc:
        return [Check("err", f"The written file does not read back: {exc}")]
    out = [Check("ok", "File reads back in astropy")]
    shape_ok = h.get("NAXIS") == 1 and h.get("BITPIX") == -32 and h.get("NAXIS1") == n == data.size
    out.append(Check("ok" if shape_ok else "err", f"1D float32, {n} pixels" if shape_ok else
                     f"Unexpected layout: NAXIS={h.get('NAXIS')} BITPIX={h.get('BITPIX')} NAXIS1={h.get('NAXIS1')}"))
    lin = float(h.get("CDELT1", 0) or 0) > 0 and "CRVAL1" in h and h.get("CUNIT1") == "Angstrom"
    out.append(Check("ok" if lin else "err", f"Linear wavelength axis: CRVAL1 {h.get('CRVAL1')} Å, "
                     f"CDELT1 {h.get('CDELT1')} Å" if lin else "CRVAL1/CDELT1/CUNIT1 missing or invalid"))
    try:
        times_ok = all(datetime.fromisoformat(str(h[k])) for k in ("DATE-OBS", "DATE-END"))
    except (KeyError, ValueError):
        times_ok = False
    out.append(Check("ok" if times_ok else "err", "DATE-OBS / DATE-END read as ISOT" if times_ok
                     else "DATE-OBS / DATE-END missing or not ISOT"))
    keys = [k for k in ("OBJNAME", "OBSERVER", "EXPTIME", "JD") if not h.get(k)]
    out.append(Check("err" if keys else "ok", f"Missing in the file: {', '.join(keys)}" if keys
                     else "OBJNAME, OBSERVER, EXPTIME, JD present"))
    bad = int((~np.isfinite(data)).sum())
    out.append(Check("ok" if bad == 0 else "warn", "All flux values finite" if bad == 0 else
                     f"{bad} pixels are NaN/inf"))
    return out


# ---- files -------------------------------------------------------------------------------------------
def export_name(objname: str, t: Times | None, observer_part: str, pattern: str = "_{obj}_{date}_{frac}_{obs}") -> str:
    """Default: the name of the accepted AAVSO file, `_<object>_<yyyymmdd>_<thousandths of day>_<observer>.fit`
    (hypothesis from that file; the AAVSO manual v1.4 sets no file-name rule)."""
    obj = re.sub(r"[^a-z0-9+\-]", "", objname.lower())
    if t is None:
        return f"_{obj}.fit"
    start = t.start
    frac = (start.hour * 3600 + start.minute * 60 + start.second + start.microsecond / 1e6) / 86400
    name = pattern.format(obj=obj, date=start.strftime("%Y%m%d"), frac=f"{round(frac * 1000):03d}",
                          obs=re.sub(r"[^A-Za-z0-9]", "", observer_part))
    return name + ".fit"


def write_export(path: Path, flux: np.ndarray, plan: ExportPlan) -> Path:
    return write_float32(path, flux, plan.header, plan.history)


def wavelengths(h: dict[str, Any], n: int) -> np.ndarray:
    return float(h["CRVAL1"]) + float(h["CDELT1"]) * (np.arange(n) + 1 - float(h.get("CRPIX1", 1)))


def write_table(path: Path, lam: np.ndarray, flux: np.ndarray) -> Path:
    """Two columns: wavelength (Å) and relative flux. CSV when the name ends in .csv, else spaces (.dat)."""
    sep = "," if path.suffix.lower() == ".csv" else " "
    head = "wavelength_A,flux" if sep == "," else "# wavelength_A flux"
    rows = [f"{lam_:.3f}{sep}{f:.6g}" for lam_, f in zip(lam, flux, strict=True) if np.isfinite(f)]
    path.write_text(head + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def site_from_frames(paths: list[Path]) -> dict[str, float] | None:
    """SITELAT/SITELONG/SITEELEV from frame headers (NINA writes them), as a suggestion for Location."""
    from .fitsio import read_header

    for p in paths:
        try:
            h = read_header(p)
        except (OSError, ValueError):
            continue
        lat, lon = h.get("SITELAT"), h.get("SITELONG")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            elev = h.get("SITEELEV")
            return {"lat": float(lat), "lon": float(lon),
                    "elev": float(elev) if isinstance(elev, (int, float)) else None}
    return None
