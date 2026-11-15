"""Synthetic SPHEREx-like cutouts for tests and offline demos (needs astropy).
Fixed star field + one star that moves 1.6 px per epoch. Three epochs at 1.28 um (one comparable group)
and one epoch at 2.0 um (must land in a different group and never be differenced with the others)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

RA, DEC, N, SCALE = 269.452, 4.693, 64, 6.15


def _wcs() -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA, DEC]
    w.wcs.crpix = [(N + 1) / 2, (N + 1) / 2]
    w.wcs.cdelt = [-SCALE / 3600, SCALE / 3600]
    return w


def _gauss(img, y, x, amp, sig=1.4):
    yy, xx = np.mgrid[: img.shape[0], : img.shape[1]]
    img += amp * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sig**2))


def make_fixture(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    stars = [(rng.uniform(6, N - 6), rng.uniform(6, N - 6), rng.uniform(20, 80)) for _ in range(25)]
    epochs = [(60867.0, 1.28), (61049.0, 1.285), (61231.0, 1.282), (61050.0, 2.0)]
    obs = []
    for i, (mjd, wave) in enumerate(epochs):
        img = rng.normal(5.0 + i, 0.3, (N, N)).astype(np.float64)  # different sky level per epoch: tests background removal
        for (y, x, a) in stars:
            _gauss(img, y, x, a)
        _gauss(img, 20 + 1.6 * (i % 3), 20 + 1.6 * (i % 3), 40)  # the mover
        flags = np.zeros((N, N), dtype=np.int32)
        flags[0:3, 0:3] = 1
        h = _wcs().to_header()
        h["EXTNAME"] = "IMAGE"
        h["DETECTOR"] = 2
        h["MJD-AVG"] = mjd
        h["EXPTIME"] = 112.6
        h["SDWAVE"] = wave
        oid = f"fx_d2_e{i + 1}"
        fits.HDUList([
            fits.PrimaryHDU(),
            fits.ImageHDU(img.astype(np.float32), header=h, name="IMAGE"),
            fits.ImageHDU(flags, name="FLAGS"),
            fits.ImageHDU(np.full((N, N), 0.09, dtype=np.float32), name="VARIANCE"),
        ]).writeto(out / f"{oid}.fits", overwrite=True)
        obs.append({"obs_id": oid, "access_url": "https://irsa.ipac.caltech.edu/fixture", "t_mjd": mjd,
                    "em_min_um": wave, "em_max_um": wave, "detector": 2, "release": "fixture"})
    (out / "observations.json").write_text(json.dumps(obs, indent=2))
    return out


if __name__ == "__main__":
    import sys

    print("wrote", make_fixture(Path(sys.argv[1] if len(sys.argv) > 1 else "data/fixtures")))
