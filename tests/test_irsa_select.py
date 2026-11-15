import pytest

from app.config import Settings
from app.errors import NoCoverage, ValidationFailed
from app.services.irsa import Observation, _detector_from, select_observations
from app.validation import SearchParams

S = Settings()


def ob(i, det, mjd, url="https://irsa.ipac.caltech.edu/x"):
    return Observation(f"o{i}", url, mjd, 1.0, 1.1, det, "r")


def test_picks_best_covered_detector_and_orders_by_time():
    obs = [ob(1, 2, 60900), ob(2, 3, 60800), ob(3, 3, 60700), ob(4, 3, 60750)]
    out = select_observations(obs, SearchParams(10, 10, 0.05), S)
    assert [o.obs_id for o in out] == ["o3", "o4", "o2"]


def test_explicit_detector_and_dedupe_and_cap():
    obs = [ob(1, 2, 60900), ob(1, 2, 60900), ob(2, 3, 60800)]
    assert [o.obs_id for o in select_observations(obs, SearchParams(10, 10, 0.05, detector=2), S)] == ["o1"]
    many = [ob(i, 2, 60000 + i) for i in range(100)]
    assert len(select_observations(many, SearchParams(10, 10, 0.05), S)) == S.max_pool


def test_no_coverage_and_size_budget():
    with pytest.raises(NoCoverage):
        select_observations([], SearchParams(10, 10, 0.05), S)
    with pytest.raises(ValidationFailed):
        select_observations([ob(i, 2, 60000 + i) for i in range(24)], SearchParams(10, 10, 0.5), S)


def test_date_filter():
    obs = [ob(1, 2, 60867.0), ob(2, 2, 61231.0)]  # 2025-07 and 2026-07 (approx)
    out = select_observations(obs, SearchParams(10, 10, 0.05, start="2026-01-01"), S)
    assert [o.obs_id for o in out] == ["o2"]


def test_detector_parse():
    assert _detector_from("SPHEREx-D3") == 3
    assert _detector_from("", "level2_2025W10_1B_0001_1_D4_spx.fits") == 4
    assert _detector_from("nothing here") is None


def test_build_query_matches_tutorial_shape():
    from app.services.irsa import build_query

    q = build_query(269.452, 4.693, 2)
    assert "spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid" in q
    assert "POINT('ICRS', 269.452, 4.693)" in q and "p.energy_bandpassname = 'SPHEREx-D2'" in q
    assert "ORDER BY p.time_bounds_lower" in q
    assert "energy_bandpassname =" not in build_query(269.452, 4.693, None)


def test_pool_is_spread_over_time_not_the_oldest():
    many = [ob(i, 2, 60000 + i) for i in range(100)]
    out = select_observations(many, SearchParams(10, 10, 0.05, max_frames=6), S)
    assert len(out) == S.max_pool
    assert out[0].obs_id == "o0" and out[-1].obs_id == "o99"  # keeps the whole baseline
