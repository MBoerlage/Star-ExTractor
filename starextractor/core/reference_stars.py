"""Spectroscopic reference-star finder.

The catalogue is our own selection from SIMBAD by documented rules (starex_refcat v0.1.0, preset balanced,
V ≤ 6.5): bright B and A main-sequence stars, each with a grade (good / ok / poor) and the reasons for it.
It is generated outside the app (docs/REFERENCE_STARS.md) and bundled as data/reference_stars.csv, with its
provenance in data/reference_stars.meta.json. The Help guide ("Where the reference stars come from") explains
the sources, rules and limits.

Ranking is never by distance alone: by default only good and ok stars within the V limit are offered, sorted by
angular distance; poor stars are added only on request and always carry their grade. Altitude, airmass and hour
angle are not considered. Emission-line stars (an "e" in the spectral type: B2Ve, B3Vne, B1Vpe, ...) are dropped on
load as a safety net (the generator never writes one): their lines would end up in the response. No network.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "reference_stars.csv"
META_PATH = _CATALOG_PATH.with_suffix(".meta.json")
GRADES = ("good", "ok", "poor")
DEFAULT_GRADES = ("good", "ok")
V_MAX = 6.5  # the catalogue's faintest star; the user may choose a brighter limit (settings)
V_MIN_SETTING = 2.0
# "e" as a peculiarity code: after the luminosity class (B2Ve, B8IIIe) or another lowercase code (Vne, Vpe,
# spe). Not inside an element symbol of a peculiar star (A7VpSrCrEu, HgMn, He-weak).
_EMISSION = re.compile(r"(?<=[IV])e|(?<![A-Za-z])e|(?<=[a-z])e")


def is_emission(sp_type: str) -> bool:
    return bool(_EMISSION.search(sp_type))


@dataclass
class ReferenceStar:
    name: str
    ra: float  # degrees, J2000
    dec: float  # degrees, J2000
    v_mag: float
    b_v: float
    sp_type: str
    # Columns 7-19 of the generated catalogue (schema 2); empty for an old six-column file.
    alt_ids: tuple[str, ...] = ()
    ebv: float | None = None
    type_source: str = ""
    n_type_measurements: int | None = None
    n_type_consistent: int | None = None
    vsini: float | None = None
    distance_pc: float | None = None
    v_class: str = ""  # normal (V ≤ 6.0) | faint (6.0 < V ≤ 6.5)
    grade: str = ""  # good | ok | poor; "" when the file has no grades
    reasons: tuple[str, ...] = field(default_factory=tuple)
    reason_text: str = ""
    verified: bool = False
    notes: str = ""


@dataclass
class ReferenceStarMatch:
    star: ReferenceStar
    distance_deg: float
    best: bool = False  # the nearest good-or-ok star of this list

    @property
    def grade(self) -> str:
        return self.star.grade

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.star.reasons

    @property
    def reason_text(self) -> str:
        return self.star.reason_text

    @property
    def v_class(self) -> str:
        return self.star.v_class

    @property
    def verified(self) -> bool:
        return self.star.verified


def _float(v: str | None) -> float | None:
    try:
        f = float(v) if v not in (None, "") else None
    except ValueError:
        return None
    return f if f is None or f == f else None


def _int(v: str | None) -> int | None:
    f = _float(v)
    return None if f is None else int(f)


def _split(v: str | None, sep: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in (v or "").split(sep) if p.strip())


def _star(row: dict[str, str]) -> ReferenceStar:
    grade = (row.get("grade") or "").strip().lower()
    return ReferenceStar(
        name=row["name"], ra=float(row["ra_deg"]), dec=float(row["dec_deg"]), v_mag=float(row["v_mag"]),
        b_v=float(row["b_v"]), sp_type=row["sp_type"],
        alt_ids=_split(row.get("alt_ids"), ";"), ebv=_float(row.get("ebv")), type_source=row.get("type_source") or "",
        n_type_measurements=_int(row.get("n_type_measurements")),
        n_type_consistent=_int(row.get("n_type_consistent")), vsini=_float(row.get("vsini")),
        distance_pc=_float(row.get("distance_pc")), v_class=(row.get("v_class") or "").strip().lower(),
        grade=grade if grade in GRADES else "", reasons=_split(row.get("reasons"), "+"),
        reason_text=(row.get("reason_text") or "").strip(),
        verified=(row.get("verified") or "").strip().lower() in ("verified", "true", "1", "yes"),
        notes=(row.get("notes") or "").strip())


@lru_cache(maxsize=4)
def _load_catalog(path: Path | None = None) -> list[ReferenceStar]:
    p = path or _CATALOG_PATH
    stars: list[ReferenceStar] = []
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if is_emission(row["sp_type"]):
                continue
            stars.append(_star(row))
    logger.debug("Loaded %d reference stars from %s", len(stars), p.name)
    return stars


@lru_cache(maxsize=4)
def _arrays(path: Path | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(ra, dec in radians, V, grade) of the catalogue, for vectorised lookups."""
    stars = _load_catalog(path)
    return (np.radians(np.array([s.ra for s in stars])), np.radians(np.array([s.dec for s in stars])),
            np.array([s.v_mag for s in stars]), np.array([s.grade for s in stars]))


@lru_cache(maxsize=1)
def meta() -> dict:
    """The provenance of the bundled list (generator version, preset, counts, retrieval date); {} if missing."""
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _separations(ra: float, dec: float, path: Path | None = None) -> np.ndarray:
    r, d, _, _ = _arrays(path)
    t_ra, t_dec = np.radians(ra), np.radians(dec)
    cos_sep = np.sin(t_dec) * np.sin(d) + np.cos(t_dec) * np.cos(d) * np.cos(t_ra - r)
    return np.degrees(np.arccos(np.clip(cos_sep, -1.0, 1.0)))


def _mask(v_max: float, grades: tuple[str, ...] | None, path: Path | None = None) -> np.ndarray:
    _, _, v, g = _arrays(path)
    m = v <= v_max + 1e-9
    if grades is not None:
        m &= np.isin(g, list(grades)) | (g == "")  # a star without a grade (old file) is never hidden
    return m


def find_reference_stars(target_ra: float, target_dec: float, n: int = 10, v_max: float = V_MAX,
                         grades: tuple[str, ...] | None = DEFAULT_GRADES,
                         path: Path | None = None) -> list[ReferenceStarMatch]:
    """The `n` nearest catalogue stars of the given grades with V ≤ v_max, sorted by distance on the sky. The
    nearest good-or-ok star among them is marked `best`. grades=None: every grade."""
    stars = _load_catalog(path)
    if not stars:
        return []
    sep = _separations(target_ra, target_dec, path)
    idx = np.flatnonzero(_mask(v_max, grades, path))
    order = idx[np.argsort(sep[idx], kind="stable")][:n]
    out = [ReferenceStarMatch(star=stars[i], distance_deg=float(sep[i])) for i in order]
    best = next((m for m in out if m.grade in DEFAULT_GRADES), None)
    if best is not None:
        best.best = True
    return out


def _nearest(ra: float, dec: float, v_max: float, grades: tuple[str, ...] | None,
             path: Path | None = None) -> ReferenceStarMatch | None:
    found = find_reference_stars(ra, dec, 1, v_max, grades, path)
    return found[0] if found else None


def best_good_or_ok(ra: float, dec: float, v_max: float = V_MAX, path: Path | None = None) -> ReferenceStarMatch | None:
    """The nearest good-or-ok star with V ≤ v_max."""
    m = _nearest(ra, dec, v_max, DEFAULT_GRADES, path)
    if m is not None:
        m.best = m.grade in DEFAULT_GRADES
    return m


def nearest_any(ra: float, dec: float, v_max: float = V_MAX, path: Path | None = None) -> ReferenceStarMatch | None:
    """The nearest star of any grade with V ≤ v_max (shown with its grade, never offered as an equal)."""
    return _nearest(ra, dec, v_max, None, path)


def nearer_poor(ra: float, dec: float, v_max: float = V_MAX, path: Path | None = None) -> ReferenceStarMatch | None:
    """The nearest poor star when it is closer than the nearest good-or-ok star, else None."""
    any_ = nearest_any(ra, dec, v_max, path)
    best = best_good_or_ok(ra, dec, v_max, path)
    if any_ is None or any_.grade != "poor":
        return None
    return any_ if best is None or any_.distance_deg < best.distance_deg else None


def _key(name: str) -> str:
    return "".join(str(name).split()).casefold()


@lru_cache(maxsize=4)
def _by_name(path: Path | None = None) -> dict[str, ReferenceStar]:
    out: dict[str, ReferenceStar] = {}
    for s in _load_catalog(path):
        for n in (s.name, *s.alt_ids):
            out.setdefault(_key(n.removeprefix("* ")), s)
    return out


def star_by_name(name: str, path: Path | None = None) -> ReferenceStar | None:
    """A catalogue star by its name or one of its other identifiers (case and spaces ignored), else None."""
    if not str(name or "").strip():
        return None
    k = _key(str(name).removeprefix("* "))
    return _by_name(path).get(k)
