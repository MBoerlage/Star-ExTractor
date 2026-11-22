"""Manual RA/Dec entry (Plan and Files)."""
from __future__ import annotations

import pytest

from starextractor.core.coords import parse_coords


def test_parse_coords_sexagesimal_and_decimal() -> None:
    ra, dec = parse_coords("18 53 02", "-28 20 00")
    assert ra == pytest.approx(283.258333, abs=1e-5) and dec == pytest.approx(-28.333333, abs=1e-5)
    assert parse_coords("18:53:02.0", "-28d20m00s") == pytest.approx((ra, dec))
    assert parse_coords("283.2583", "-28.3333") == pytest.approx((283.2583, -28.3333))


@pytest.mark.parametrize("ra, dec", [("25 00 00", "+10 00 00"), ("10 00 00", "+95 00 00"), ("", "+10"), ("abc", "1")])
def test_parse_coords_rejects_bad_input(ra: str, dec: str) -> None:
    with pytest.raises(ValueError):
        parse_coords(ra, dec)
