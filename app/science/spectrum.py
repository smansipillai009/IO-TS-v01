"""On-the-fly spectrum: for one sky position, one aperture measurement per cutout, each with the wavelength
at THAT pixel and the time of THAT exposure. Points are not simultaneous; the UI says so.
Units: MJy/sr aperture mean, local-background subtracted (annulus median). Conversion to Jy (x pixel solid
angle x N pixels) is deferred; verify the image BUNIT on Day 1."""
from __future__ import annotations

import numpy as np

from .fits_io import Cutout, inside, target_pixel, wavelength_at

FLUX_UNIT = "MJy/sr (aperture mean, background-subtracted)"


def extract_point(c: Cutout, ra: float, dec: float, aperture_arcsec: float) -> dict | None:
    """None when the position is off the image, the aperture is mostly masked, or there is no wavelength WCS."""
    x, y = target_pixel(c, ra, dec)
    if not inside(c, x, y):
        return None
    r = max(aperture_arcsec / c.pixel_scale_arcsec, 1.5)
    h, w = c.image.shape
    yy, xx = np.ogrid[:h, :w]
    rr = np.hypot(xx - x, yy - y)
    ap = rr <= r
    ann = (rr >= 1.5 * r) & (rr <= 2.5 * r)
    good = ap & np.isfinite(c.image)
    if ap.sum() < 1 or good.sum() < 0.5 * ap.sum():
        return None
    ring = c.image[ann & np.isfinite(c.image)]
    bkg = float(np.median(ring)) if ring.size >= 10 else 0.0
    flux = float(np.mean(c.image[good]) - bkg)
    err = None
    if c.variance is not None:
        v = c.variance[good]
        v = v[np.isfinite(v)]
        if v.size:
            # assumes independent pixels; correlated noise makes this an underestimate
            err = float(np.sqrt(v.sum()) / good.sum())
    try:
        wave, band = wavelength_at(c, x, y)
    except ValueError:
        return None
    return {
        "wavelength_um": wave,
        "bandpass_um": band,
        "flux": flux,
        # JSON has no NaN. 0.0 + flux_err_known=false means "no VARIANCE available", not "zero error".
        "flux_err": err if err is not None else 0.0,
        "flux_err_known": err is not None,
        "observation_time_utc": c.time_utc,
        "detector": c.detector,
        "obs_id": c.obs_id,
    }
