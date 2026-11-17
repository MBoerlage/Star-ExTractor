"""Spectroscopic reference-star finder.

Ported from SpectroTargetTool (itself from F. Teyssier's "Reference Star Finder" spreadsheet, v3 2024, built
from SIMBAD): given a target's RA/Dec, find the nearest bright, well-characterised stars from a bundled catalog
so the observer can pick a flux/telluric calibration star close to the target on sky. Ranking is by angular
distance only (no altitude, airmass, hour angle or reddening).

Catalog: ~1000 V<6 A/B-type main-sequence stars (name, RA/Dec, V, B-V, spectral type),
bundled as data/reference_stars.csv. Emission-line stars (an "e" in the spectral type: B2Ve, B3Vne,
B1Vpe, ...) are dropped on load and never offered: their lines would end up in the response.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "reference_stars.csv"
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


@dataclass
class ReferenceStarMatch:
    star: ReferenceStar
    distance_deg: float


@lru_cache(maxsize=1)
def _load_catalog() -> list[ReferenceStar]:
    stars: list[ReferenceStar] = []
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if is_emission(row["sp_type"]):
                continue
            stars.append(ReferenceStar(
                name=row["name"],
                ra=float(row["ra_deg"]),
                dec=float(row["dec_deg"]),
                v_mag=float(row["v_mag"]),
                b_v=float(row["b_v"]),
                sp_type=row["sp_type"],
            ))
    logger.debug("Loaded %d reference stars from %s", len(stars), _CATALOG_PATH.name)
    return stars


def find_reference_stars(target_ra: float, target_dec: float, n: int = 10) -> list[ReferenceStarMatch]:
    """Return the `n` catalog stars closest on sky to (target_ra, target_dec)."""
    stars = _load_catalog()
    if not stars:
        return []

    ra = np.radians(np.array([s.ra for s in stars]))
    dec = np.radians(np.array([s.dec for s in stars]))
    t_ra = np.radians(target_ra)
    t_dec = np.radians(target_dec)

    cos_sep = np.sin(t_dec) * np.sin(dec) + np.cos(t_dec) * np.cos(dec) * np.cos(t_ra - ra)
    cos_sep = np.clip(cos_sep, -1.0, 1.0)
    sep_deg = np.degrees(np.arccos(cos_sep))

    order = np.argsort(sep_deg)[:n]
    return [ReferenceStarMatch(star=stars[i], distance_deg=float(sep_deg[i])) for i in order]
