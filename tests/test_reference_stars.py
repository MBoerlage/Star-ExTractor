"""Tests for the spectroscopic reference-star finder (core/reference_stars.py) on the bundled list."""

from starextractor.core.reference_stars import _load_catalog, find_reference_stars


class TestCatalog:
    def test_loads_a_large_bright_star_catalog(self):
        stars = _load_catalog()
        assert len(stars) > 500
        assert all(s.v_mag < 7 for s in stars)
        assert all(s.name for s in stars)


class TestFindReferenceStars:
    def test_returns_n_results_sorted_by_distance(self):
        matches = find_reference_stars(target_ra=180.0, target_dec=0.0, n=10)
        assert len(matches) == 10
        distances = [m.distance_deg for m in matches]
        assert distances == sorted(distances)
        assert all(d >= 0 for d in distances)

    def test_exact_catalog_position_returns_itself_first(self):
        # HD 224926 is the catalogue's first entry (graded poor: every grade is asked for)
        matches = find_reference_stars(target_ra=0.456034, target_dec=-3.027503, n=5, grades=None)
        assert matches[0].star.name == "HD 224926"
        assert matches[0].distance_deg < 1e-3

    def test_result_fields_populated(self):
        matches = find_reference_stars(target_ra=100.0, target_dec=20.0, n=3)
        for m in matches:
            assert m.star.ra >= 0
            assert -90 <= m.star.dec <= 90
            assert m.star.v_mag is not None
            assert m.star.sp_type


def test_catalog_spectral_types_with_decimal_commas_map_to_templates():
    from starextractor.core.response import parse_sptype

    assert parse_sptype("B2,5V") == ("B", 2.5, "V")
    assert parse_sptype("B9,5V+,,,") == ("B", 9.5, "V")
    assert sum(parse_sptype(s.sp_type) is None for s in _load_catalog()) == 0  # every catalog type parses


def test_emission_line_stars_are_never_offered():
    import csv

    from starextractor.core.reference_stars import _CATALOG_PATH, is_emission

    for sp in ("B2Ve", "B3Vne", "B1Vpe", "B6Vep", "B4Vnpe", "A2Vspe,,,", "B7Ve+,,,", "B8IIIe", "B2(e)"):
        assert is_emission(sp), sp
    for sp in ("B2V", "A0Vn", "A7VpSrCrEu", "B9pHgMn", "B3VpHe", "B2,5V", "A1Vnn"):
        assert not is_emission(sp), sp
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    stars = _load_catalog()
    assert len(stars) == len(rows) - sum(is_emission(r["sp_type"]) for r in rows) == 1595  # none dropped
    assert not any(is_emission(s.sp_type) for s in stars)
    # all-sky sweep: no emission star ever comes back
    for ra in range(0, 360, 30):
        for dec in (-60, -30, 0, 30, 60):
            assert not any(is_emission(m.star.sp_type) for m in find_reference_stars(ra, dec, 50, grades=None))


def test_saved_emission_reference_is_cleared():
    from starextractor.core.plan import plan_state
    from starextractor.core.session import Session

    s = Session(None)
    s.params["plan"] = {"reference": {"name": "HD 5394", "sp_type": "B0,5Ve", "chosen": True}}
    assert plan_state(s)["reference"] == {}
