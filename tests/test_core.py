"""Pure-logic tests: no network, no FITS, no web framework needed."""
import numpy as np
import pytest
from scipy import ndimage as ndi

from app.config import Settings
from app.errors import ValidationFailed
from app.science.align import alignment_status, measure_shift
from app.science.grouping import group_frames
from app.science.render import diverging_rgb, stretch_to_uint8
from app.validation import request_id, validate_search

S = Settings()


def v(**kw):
    d = dict(ra=269.452, dec=4.693, radius=0.05, start=None, end=None, detector=None, s=S)
    d.update(kw)
    return validate_search(**d)


def test_valid_ok():
    assert v().radius == 0.05


@pytest.mark.parametrize("kw", [
    dict(ra=360.0), dict(ra=-1), dict(dec=91), dict(dec=-91), dict(radius=0), dict(radius=0.6),
    dict(ra=float("nan")), dict(start="2025-13-01"), dict(start="2026-01-02", end="2026-01-01"), dict(detector=7),
])
def test_invalid_rejected(kw):
    with pytest.raises(ValidationFailed):
        v(**kw)


def test_request_id_deterministic_and_release_sensitive():
    a = request_id(v(), "r1", "0.1.0")
    assert a == request_id(v(ra=269.452000001), "r1", "0.1.0")  # rounding to 5 dp
    assert a != request_id(v(), "r2", "0.1.0")
    assert a != request_id(v(), "r1", "0.2.0")
    assert a != request_id(v(radius=0.1), "r1", "0.1.0")


def test_grouping_by_detector_and_wavelength():
    fr = [
        dict(frame_id="a", detector=2, wavelength_um=1.280),
        dict(frame_id="b", detector=2, wavelength_um=1.285),
        dict(frame_id="c", detector=2, wavelength_um=1.60),
        dict(frame_id="d", detector=4, wavelength_um=1.281),
    ]
    g = group_frames(fr, 0.02)
    ids = sorted(tuple(x["frame_ids"]) for x in g)
    assert ids == [("a", "b"), ("c",), ("d",)]  # never mixes detectors or distant wavelengths
    assert g[0]["group_id"] == "d2-1.28"


def _field(seed=0, n=40, size=128):
    r = np.random.default_rng(seed)
    img = r.normal(0, 0.05, (size, size))
    for _ in range(n):
        y, x = r.integers(8, size - 8, 2)
        img[y, x] += r.uniform(5, 20)
    return ndi.gaussian_filter(img, 1.5)


def test_alignment_detects_shift_and_passes_identity():
    f = _field()
    assert alignment_status(f, f.copy(), 0.5, 1.5)[0] == "good"
    shifted = ndi.shift(f, (2.0, -1.0), order=1)
    st, res = alignment_status(f, shifted, 0.5, 1.5)
    assert st == "failed" and 2.0 < res < 2.5
    dy, dx, _ = measure_shift(f, ndi.shift(f, (0.4, 0.0), order=1))
    assert abs(abs(dy) - 0.4) < 0.25


def test_alignment_unknown_on_noise():
    r = np.random.default_rng(1)
    st, res = alignment_status(r.normal(size=(128, 128)), r.normal(size=(128, 128)), 0.5, 1.5)
    assert st == "unknown" and res is None


@pytest.mark.parametrize("k", ["linear", "asinh", "zscale"])
def test_stretch_handles_nan(k):
    a = _field()
    a[:10] = np.nan
    u = stretch_to_uint8(a, k)
    assert u.dtype == np.uint8 and (u[:10] == 0).all() and u.max() > 0


def test_diverging_signs():
    d = np.zeros((20, 20)); d[5, 5] = 50; d[10, 10] = -50; d[0, 0] = np.nan
    r = diverging_rgb(d)
    assert r[5, 5, 0] == 255 and r[5, 5, 2] < 100      # brighter -> red
    assert r[10, 10, 2] == 255 and r[10, 10, 0] < 100  # fainter -> blue
    assert r[0, 0, 3] == 0                              # masked -> transparent
