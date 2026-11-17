"""Fast FITS access: dependency-free header reader, memmap pixel access, astropy writers.

Reading never decodes the whole file: the primary data unit is memory-mapped with its on-disk
big-endian dtype and BZERO/BSCALE are applied per strip as float32. Trailing padding after the
data unit (seen in BASS files) is ignored. Originals are never written; new files go through
astropy (float32 2D stacks/masters, 1D spectra).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BLOCK = 2880
CARD = 80
_COMMENTARY = {"HISTORY", "COMMENT", ""}
_DTYPES = {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}
STRUCTURAL = {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "EXTEND", "BZERO",
              "BSCALE", "BLANK", "END", "HISTORY", "COMMENT", ""}


class FitsError(ValueError):
    pass


def _read_header_bytes(fh) -> bytes:
    out = bytearray()
    while True:
        block = fh.read(BLOCK)
        if len(block) < BLOCK:
            raise FitsError("truncated FITS header (no END card)")
        if not out and not block.startswith(b"SIMPLE  ="):
            raise FitsError("not a FITS file (no SIMPLE card)")
        out += block
        for i in range(0, BLOCK, CARD):
            if block[i:i + 8] == b"END     ":
                return bytes(out)


def _cards(raw: bytes) -> list[str]:
    cards = []
    for i in range(0, len(raw), CARD):
        card = raw[i:i + CARD].decode("ascii", errors="replace")
        if card.startswith("END     ") or card.rstrip() == "END":
            break
        cards.append(card)
    return cards


def parse_value(card: str) -> Any:
    """Value field of one 80-char card; None for commentary cards or empty values."""
    key = card[:8].strip()
    if key in _COMMENTARY or card[8:10] != "= ":
        return None
    v = card[10:].lstrip()
    if v.startswith("'"):
        s, i = [], 1
        while i < len(v):
            c = v[i]
            if c == "'":
                if i + 1 < len(v) and v[i + 1] == "'":
                    s.append("'")
                    i += 2
                    continue
                break
            s.append(c)
            i += 1
        return "".join(s).rstrip()
    v = v.split("/", 1)[0].strip()
    if v == "":
        return None
    if v in ("T", "F"):
        return v == "T"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v.replace("D", "E").replace("d", "e"))
    except ValueError:
        return v


@dataclass
class HeaderInfo:
    cards: dict[str, Any]
    history: list[str]
    data_offset: int


def read_header_info(path: str | os.PathLike) -> HeaderInfo:
    with open(path, "rb") as fh:
        raw = _read_header_bytes(fh)
    cards: dict[str, Any] = {}
    history: list[str] = []
    for card in _cards(raw):
        key = card[:8].strip()
        if key == "HISTORY":
            history.append(card[8:].strip())
        elif key not in _COMMENTARY and key not in cards:
            cards[key] = parse_value(card)
    return HeaderInfo(cards, history, len(raw))


def read_header(path: str | os.PathLike) -> dict[str, Any]:
    """Primary header as an ordered {KEY: value} dict (first occurrence wins)."""
    return read_header_info(path).cards


@dataclass
class Image:
    """Memory-mapped 2D primary image. `strip(y0, y1)` returns calibrated float32 rows."""

    path: Path
    raw: np.ndarray
    bscale: float
    bzero: float
    header: dict[str, Any]

    @property
    def shape(self) -> tuple[int, int]:
        return self.raw.shape  # type: ignore[return-value]

    def strip(self, y0: int, y1: int, out: np.ndarray | None = None) -> np.ndarray:
        block = self.raw[y0:y1]
        if out is None:
            out = np.empty(block.shape, np.float32)
        np.copyto(out, block, casting="unsafe")
        if self.bscale != 1.0:
            out *= np.float32(self.bscale)
        if self.bzero != 0.0:
            out += np.float32(self.bzero)
        return out

    def read(self) -> np.ndarray:
        return self.strip(0, self.shape[0])


def open_image(path: str | os.PathLike) -> Image:
    p = Path(path)
    info = read_header_info(p)
    h = info.cards
    if h.get("NAXIS") != 2:
        raise FitsError(f"{p.name}: expected a 2-D image, NAXIS={h.get('NAXIS')}")
    bitpix = int(h["BITPIX"])
    if bitpix not in _DTYPES:
        raise FitsError(f"{p.name}: unsupported BITPIX {bitpix}")
    shape = (int(h["NAXIS2"]), int(h["NAXIS1"]))
    need = info.data_offset + shape[0] * shape[1] * abs(bitpix) // 8
    if p.stat().st_size < need:
        raise FitsError(f"{p.name}: file is shorter than its header says (truncated?)")
    raw = np.memmap(p, dtype=_DTYPES[bitpix], mode="r", offset=info.data_offset, shape=shape)
    return Image(p, raw, float(h.get("BSCALE", 1) or 1), float(h.get("BZERO", 0) or 0), h)


def read_image(path: str | os.PathLike) -> np.ndarray:
    return open_image(path).read()


def peak_adu(img: Image, strip_rows: int = 256) -> float:
    """Brightest real signal: max of a 2×2 minimum filter, so isolated hot pixels (65520) are
    ignored while a saturated spectrum (several pixels wide) is still seen."""
    best = -np.inf
    h = img.shape[0]
    prev_last: np.ndarray | None = None
    for y0 in range(0, h, strip_rows):
        block = img.strip(y0, min(h, y0 + strip_rows))
        if prev_last is not None:
            block = np.vstack([prev_last, block])
        prev_last = block[-1:].copy()
        m = np.minimum(block[:, :-1], block[:, 1:])
        m = np.minimum(m[:-1], m[1:])
        if m.size:
            best = max(best, float(m.max()))
    return best


# ---- writing ---------------------------------------------------------------------------------
_ASCII = str.maketrans({"×": "x", "−": "-", "–": "-", "—": "-", "°": "deg", "Å": "A", "·": "-",
                        "…": "...", "σ": "sigma", "α": "alpha", "β": "beta", "γ": "gamma",
                        "δ": "delta", "ε": "epsilon", "µ": "u", "→": "->"})


def ascii_text(text: str) -> str:
    """Printable ASCII for FITS cards: common symbols spelled out, anything else dropped."""
    return "".join(c for c in text.translate(_ASCII) if 32 <= ord(c) < 127)


def _safe_cards(header: dict[str, Any]) -> list[tuple[str, Any]]:
    out = []
    for k, v in header.items():
        if k in STRUCTURAL or v is None or len(k) > 8:
            continue
        out.append((k, ascii_text(v) if isinstance(v, str) else v))
    return out


def write_float32(path: str | os.PathLike, data: np.ndarray, header: dict[str, Any] | None = None,
                  history: list[str] | tuple[str, ...] = (), comments: dict[str, str] | None = None) -> Path:
    """Write a new float32 image or spectrum (atomic: temp file then rename)."""
    from astropy.io import fits

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.ascontiguousarray(data, dtype=np.float32))
    comments = comments or {}
    for k, v in _safe_cards(header or {}):
        try:
            hdu.header[k] = (v, comments.get(k, ""))
        except (ValueError, TypeError):
            continue
    for line in map(ascii_text, history):
        for i in range(0, max(len(line), 1), 70):
            hdu.header.add_history(line[i:i + 70])
    tmp = p.with_name(p.stem + ".part" + p.suffix)
    hdu.writeto(tmp, overwrite=True)
    os.replace(tmp, p)
    return p
