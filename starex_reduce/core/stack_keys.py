"""The SX* keyword block of a stacked image (Target_S.fit / Reference_S.fit): the rows chosen in Acquisition
and a few stack facts, so a stack describes itself without the sets file (docs/DATA.md "Stack keywords").

Rows convention: 1-based, inclusive, counted along the image Y axis (NAXIS2) exactly as the pixels are stored
in the stack file. The stack is never flipped or rotated, so this is the camera frame: row r is numpy row r-1,
the frame Geometry reads. A band with no selection has no keywords. The block is private to this program: it is
stripped from every file written after the stack (Geometry onwards) and from every exported header. No Qt.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .extract import Band, Bands

SX_VERSION = 1
PREFIX = "SX"
BANDS = (("spec", "SXROW1", "SXROW2", "spectrum"), ("sky_a", "SXSKA1", "SXSKA2", "sky A"),
         ("sky_b", "SXSKB1", "SXSKB2", "sky B"))
ROW_KEYS = tuple(k for _, a, b, _ in BANDS for k in (a, b))
COMMENTS = {
    "SXVER": "version of the SX* stack keyword block",
    "SXNSUB": "number of subs stacked",
    "SXSUBEXP": "[s] exposure of one sub",
    "SXNAME": "object name chosen in Acquisition",
    "SXRAWDIR": "folder of the raw subs (absolute)",
    "SXDARK": "dark master, path in the Masters folder",
    "SXDARKH": "SHA256 of the dark master",
    "SXBIAS": "bias master, path in the Masters folder",
    "SXBIASH": "SHA256 of the bias master",
    "SXFLAT": "flat master, path in the Masters folder",
    "SXFLATH": "SHA256 of the flat master",
}
MASTER_KEYS = {"dark": ("SXDARK", "SXDARKH"), "bias": ("SXBIAS", "SXBIASH"), "flat": ("SXFLAT", "SXFLATH")}
for _key, _a, _b, _what in BANDS:
    COMMENTS[_a] = f"{_what} first row, 1-based incl., file Y axis"
    COMMENTS[_b] = f"{_what} last row, 1-based incl., file Y axis"

Rows1 = dict[str, tuple[int, int]]  # {"spec" | "sky_a" | "sky_b": (first, last)}, 1-based inclusive


def is_sx(key: str) -> bool:
    return key.upper().startswith(PREFIX) and len(key) <= 8 and key.upper() in _ALL


_ALL = {"SXVER", "SXNSUB", "SXSUBEXP", "SXNAME", "SXRAWDIR", *ROW_KEYS,
        *(k for pair in MASTER_KEYS.values() for k in pair)}


def strip_sx(header: dict[str, Any]) -> dict[str, Any]:
    """A copy of `header` without the SX* block (for every header copied downstream of the stack)."""
    return {k: v for k, v in header.items() if not is_sx(k)}


# ---- rows: Acquisition's locked rows <-> 1-based file rows ---------------------------------------------------
def bands_of_rows(rows: dict[str, Any]) -> Bands:
    """The bands Acquisition draws for its locked rows (0-based rows): round(ridge row) + each offset.
    The same formula as the Acquisition overlay, so header rows and the overlay are the same pixels."""
    r0 = round(float(rows["row"]))
    return Bands(*(Band(r0 + int(rows[k][0]), r0 + int(rows[k][1])) for k in ("spec", "sky_a", "sky_b")))


def rows1_of(rows: dict[str, Any] | None) -> Rows1:
    """1-based inclusive file rows from Acquisition's rows (Rows.to_json()), {} when there are none."""
    if not rows or rows.get("row") is None or not rows.get("spec"):
        return {}
    b = bands_of_rows(rows)
    out: Rows1 = {}
    for key, band in (("spec", b.spectrum), ("sky_a", b.sky_a), ("sky_b", b.sky_b)):
        if rows.get(key):
            out[key] = (band.top + 1, band.bottom + 1)
    return out


def bands0(rows1: Rows1) -> dict[str, Band]:
    """0-based numpy bands of the file rows (what a drawing of them covers)."""
    return {k: Band(a - 1, b - 1) for k, (a, b) in rows1.items()}


def offsets_of_rows1(rows1: Rows1, flip_y: bool) -> dict[str, list[Any]] | None:
    """Extract's offsets from the file rows: band offsets from the spectrum's middle row, plus 'near' (that row in
    the stack frame and the Flip Y setting) so Extract looks for the spectrum there."""
    if "spec" not in rows1:
        return None
    b = bands0(rows1)
    r = (b["spec"].top + b["spec"].bottom) // 2
    off: dict[str, list[Any]] = {}
    for k in ("spec", "sky_a", "sky_b"):
        band = b.get(k)
        if band is None:  # no sky band chosen: the default gap and width next to the spectrum
            h = b["spec"].bottom - b["spec"].top + 1
            band = Band(b["spec"].top - 10 - h, b["spec"].top - 11) if k == "sky_a" else \
                Band(b["spec"].bottom + 11, b["spec"].bottom + 10 + h)
        off[k] = [band.top - r, band.bottom - r]
    off["near"] = [float(r), 1.0 if flip_y else 0.0]
    return off


# ---- header cards ----------------------------------------------------------------------------------------------
def sx_cards(name: str, nsub: int, subexp: float | None, rows1: Rows1) -> dict[str, Any]:
    h: dict[str, Any] = {"SXVER": SX_VERSION, "SXNSUB": int(nsub)}
    if subexp is not None:
        h["SXSUBEXP"] = float(subexp)
    if name:
        h["SXNAME"] = name
    for key, a, b, _ in BANDS:
        if key in rows1:
            h[a], h[b] = int(rows1[key][0]), int(rows1[key][1])
    return h


def masters_cards(masters: dict[str, tuple[str, str]]) -> dict[str, str]:
    """{kind: (path in the Masters folder, sha256)} → SXDARK/SXDARKH, SXBIAS/SXBIASH, SXFLAT/SXFLATH."""
    h: dict[str, str] = {}
    for kind, (path, sha) in masters.items():
        k_path, k_sha = MASTER_KEYS[kind]
        h[k_path], h[k_sha] = path, sha
    return h


def masters_from_header(h: dict[str, Any]) -> dict[str, tuple[str, str]]:
    out = {}
    for kind, (k_path, k_sha) in MASTER_KEYS.items():
        if h.get(k_path):
            out[kind] = (str(h[k_path]), str(h.get(k_sha) or ""))
    return out


def rows_from_header(h: dict[str, Any]) -> Rows1:
    out: Rows1 = {}
    for key, a, b, _ in BANDS:
        try:
            first, last = int(h[a]), int(h[b])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= first <= last:
            out[key] = (first, last)
    return out


def update_rows(path: Path, rows1: Rows1) -> None:
    """Rewrite only the row keywords of a stack (header-only; the pixel values are untouched). Atomic: a temp
    file next to it, then replace. Raises OSError when the file is missing or locked."""
    from astropy.io import fits

    p = Path(path)
    tmp = p.with_name(p.stem + ".part" + p.suffix)
    with fits.open(p, memmap=False, do_not_scale_image_data=True) as hdul:
        hdr = hdul[0].header
        for k in ROW_KEYS:
            hdr.remove(k, ignore_missing=True)
        hdr["SXVER"] = (SX_VERSION, COMMENTS["SXVER"])
        for key, a, b, _ in BANDS:
            if key in rows1:
                hdr[a] = (int(rows1[key][0]), COMMENTS[a])
                hdr[b] = (int(rows1[key][1]), COMMENTS[b])
        hdul.writeto(tmp, overwrite=True, output_verify="silentfix")
    os.replace(tmp, p)
