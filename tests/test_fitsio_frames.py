"""FITS reader/writer and frame typing (ported from specinti_prep tests and extended)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from starex_reduce.core import coords
from starex_reduce.core.fitsio import (
    FitsError,
    ascii_text,
    open_image,
    peak_adu,
    read_header,
    read_header_info,
    write_float32,
)
from starex_reduce.core.frames import (
    BIAS,
    DARK,
    DARKFLAT,
    FLAT,
    LIGHT,
    FrameInfo,
    load_frame,
    normalize_type,
    parse_time,
    scan,
    summary_line,
)

SAMPLE = dict(IMAGETYP="Light", EXPTIME=300.0, GAIN=200, OFFSET=3, XBINNING=1, YBINNING=1,
              INSTRUME="ZWO ASI585MM Pro", OBJECT="Sgrnov32026")


def make_fits(path: Path, shape: tuple[int, int] = (6, 8), seed: int = 0, data: np.ndarray | None = None,
              **cards: object) -> Path:
    """16-bit unsigned FITS (BITPIX=16, BZERO=32768) like SharpCap writes."""
    if data is None:
        data = np.random.default_rng(seed).integers(0, 65535, size=shape, dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    for k, v in cards.items():
        hdu.header[k.replace("_", "-")] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(path)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_header_matches_astropy(tmp_path: Path) -> None:
    p = make_fits(tmp_path / "a.fits", CCD_TEMP=-5.5, SET_TEMP=-10.0, **SAMPLE)
    h = read_header(p)
    assert h["BITPIX"] == 16 and h["BZERO"] == 32768
    assert h["EXPTIME"] == 300.0 and h["GAIN"] == 200 and h["OFFSET"] == 3
    assert h["CCD-TEMP"] == -5.5 and h["OBJECT"] == "Sgrnov32026" and h["SIMPLE"] is True


def test_not_fits_and_truncated(tmp_path: Path) -> None:
    p = tmp_path / "x.fits"
    p.write_bytes(b"hello" * 1000)
    with pytest.raises(FitsError):
        read_header(p)
    good = make_fits(tmp_path / "g.fits", shape=(100, 100))
    cut = tmp_path / "cut.fits"
    cut.write_bytes(good.read_bytes()[:2880 + 1000])
    with pytest.raises(FitsError, match="shorter"):
        open_image(cut)


def test_memmap_bzero_matches_astropy_and_never_writes(tmp_path: Path) -> None:
    p = make_fits(tmp_path / "a.fits", shape=(50, 70), seed=3, **SAMPLE)
    before = sha(p)
    img = open_image(p)
    ours = img.read()
    assert ours.dtype == np.float32 and ours.flags.c_contiguous
    np.testing.assert_array_equal(ours, fits.getdata(p).astype(np.float32))
    np.testing.assert_array_equal(img.strip(10, 20), ours[10:20])
    del img
    assert sha(p) == before


def test_trailing_padding_tolerated(tmp_path: Path) -> None:
    """BASS files carry extra bytes after the data unit."""
    p = make_fits(tmp_path / "a.fits", shape=(20, 30), seed=1)
    with open(p, "ab") as fh:
        fh.write(b"\0" * 5000)
    np.testing.assert_array_equal(open_image(p).read(), fits.getdata(p).astype(np.float32))


def test_float32_bigendian_master(tmp_path: Path) -> None:
    data = np.random.default_rng(2).normal(size=(40, 60)).astype(np.float32)
    p = write_float32(tmp_path / "m.fits", data, {"IMAGETYP": "DARK", "NOTE": "dark − bias ×1.8"},
                      ["scaled ×0.556 at 5 °C"])
    np.testing.assert_array_equal(open_image(p).read(), data)
    info = read_header_info(p)
    assert info.cards["NOTE"] == "dark - bias x1.8"
    assert "scaled x0.556 at 5 degC" in info.history
    assert not list(tmp_path.glob("*.part*"))


def test_ascii_text() -> None:
    assert ascii_text("Hα 6563 Å · σ") == "Halpha 6563 A - sigma"


def test_peak_ignores_hot_pixels_but_sees_spectrum(tmp_path: Path) -> None:
    data = np.full((300, 400), 32768 + 400, np.uint16) - 32768
    data[50, 60] = 65520  # isolated hot pixel
    data[150:156, 100:300] = 30000  # spectrum, several rows wide
    p = make_fits(tmp_path / "s.fits", data=data.astype(np.uint16))
    assert peak_adu(open_image(p)) == pytest.approx(30000)
    data[150:156, 200:210] = 64000
    p2 = make_fits(tmp_path / "s2.fits", data=data.astype(np.uint16))
    assert peak_adu(open_image(p2)) == pytest.approx(64000)


@pytest.mark.parametrize("v,t", [("Light", LIGHT), ("Light Frame", LIGHT), ("LIGHT", LIGHT), ("DARK", DARK),
                                 ("Dark Frame", DARK), ("BIAS", BIAS), ("Offset", BIAS), ("Flat", FLAT),
                                 ("DarkFlat", DARKFLAT), ("Dark Flat", DARKFLAT), ("", None), (None, None)])
def test_normalize_type(v: object, t: str | None) -> None:
    assert normalize_type(v) == t


def test_type_precedence_header_then_name_then_role(tmp_path: Path) -> None:
    f = FrameInfo(tmp_path / "x.fits", header={}, role=LIGHT)
    assert f.frame_type == LIGHT
    f = FrameInfo(tmp_path / "Bias_001.fits", header={}, role=LIGHT)
    assert f.frame_type == BIAS
    f.header["IMAGETYP"] = "DARK"
    assert f.frame_type == DARK


def test_nina_dialect_and_times(tmp_path: Path) -> None:
    p = make_fits(tmp_path / "2025-06-04_11-19-21__-7.50_300.00s_0000.fits", IMAGETYP="DARK",
                  EXPOSURE=300.0, GAIN=251, OFFSET=20, DATE_OBS="2025-06-04T16:19:21.2890921")
    f = load_frame(p)
    assert f.frame_type == DARK and f.exptime == 300.0 and f.gain == 251
    assert f.start is not None and f.start.microsecond == 289092
    assert f.end is not None and (f.end - f.start).total_seconds() == 300.0
    assert parse_time("2026-08-21T03:42:58Z") is not None
    assert parse_time("21/08/2026") is None


def test_bias_minimum_exposure(tmp_path: Path) -> None:
    p = make_fits(tmp_path / "b.fits", IMAGETYP="BIAS", EXPTIME=0.001, EXPOSURE=0.001)
    assert load_frame(p).exptime == 0.001


def test_scan_sorts_by_time_and_reports_bad(tmp_path: Path) -> None:
    make_fits(tmp_path / "b.fits", DATE_OBS="2026-08-21T03:50:00", **SAMPLE)
    make_fits(tmp_path / "a.fits", DATE_OBS="2026-08-21T03:55:00", **SAMPLE)
    (tmp_path / "broken.fits").write_bytes(b"x" * 100)
    (tmp_path / "notes.txt").write_text("x")
    frames = scan(tmp_path)
    assert [f.path.name for f in frames[:2]] == ["b.fits", "a.fits"]
    assert frames[2].error
    assert summary_line(frames).startswith("300 s · gain 200")


def test_coords() -> None:
    assert coords.parse_ra("18 00 00") == pytest.approx(270.0)
    assert coords.parse_ra("18h30m00s") == pytest.approx(277.5)
    assert coords.parse_dec("-25 30 00") == pytest.approx(-25.5)
    assert coords.parse_dec("-0:30:00") == pytest.approx(-0.5)
    with pytest.raises(ValueError):
        coords.parse_dec("95 00 00")
    assert coords.format_ra(coords.parse_ra("18 36 56.3")) == "18 36 56.3"
    assert coords.format_dec(coords.parse_dec("+38 47 01")) == "+38 47 01"
    assert coords.format_dec(-0.5) == "-00 30 00"
