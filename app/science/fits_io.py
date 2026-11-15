"""Read a SPHEREx cutout: IMAGE, FLAGS, VARIANCE, celestial WCS and the per-pixel spectral WCS.

ASSUMPTIONS TO VERIFY ON DAY 1 (run scripts/day1_spike.py on one real cutout and compare):
  * extension names IMAGE / FLAGS / VARIANCE (the reader falls back to the first 2D image HDU)
  * the spectral WCS is WCS key 'W' on the IMAGE header, evaluated with pixel_to_world(x, y)
  * header keywords DETECTOR and MJD-AVG (MJD-OBS as fallback)
Anything that cannot be read raises ValueError with a clear message; frames are SKIPPED and reported
in the manifest ('skipped'), never silently given a guessed wavelength."""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.time import Time
from astropy.utils.exceptions import AstropyWarning
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales


@dataclass
class Cutout:
    obs_id: str
    path: Path
    image: np.ndarray  # float32, bad pixels = NaN
    variance: np.ndarray | None
    wcs: WCS  # celestial
    spec_wcs: WCS | None
    mjd: float
    time_utc: str
    detector: int
    exposure_s: float | None
    bad_fraction: float
    pixel_scale_arcsec: float
    fixture_wave: float | None = None  # TEST FIXTURES ONLY (header SDWAVE); real SPHEREx files never have it


def _ext(hdul: fits.HDUList, name: str):
    for h in hdul:
        if (h.name or "").upper() == name:
            return h
    return None


def _first_image_hdu(hdul: fits.HDUList):
    for h in hdul:
        if getattr(h, "data", None) is not None and getattr(h.data, "ndim", 0) == 2:
            return h
    return None


def open_cutout(path: Path, obs_id: str, flag_bits: int, detector_hint: int | None = None) -> Cutout:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AstropyWarning)
        with fits.open(path, memmap=False) as hdul:
            img_hdu = _ext(hdul, "IMAGE") or _first_image_hdu(hdul)
            if img_hdu is None or img_hdu.data is None:
                raise ValueError("no 2D image extension found")
            hdr = img_hdu.header
            image = np.array(img_hdu.data, dtype=np.float32)
            flags_hdu, var_hdu = _ext(hdul, "FLAGS"), _ext(hdul, "VARIANCE")
            flags = np.array(flags_hdu.data, dtype=np.int64) if flags_hdu is not None and flags_hdu.data is not None else None
            var = np.array(var_hdu.data, dtype=np.float32) if var_hdu is not None and var_hdu.data is not None else None

            wcs = WCS(hdr, fobj=hdul).celestial
            if not wcs.has_celestial:
                raise ValueError("no celestial WCS")
            spec_wcs = _spectral_wcs(hdr, hdul)

            primary = hdul[0].header
            mjd = _first_key([hdr, primary], ["MJD-AVG", "MJD-OBS", "MJD_AVG"])
            if mjd is None:
                raise ValueError("no observation time (MJD-AVG / MJD-OBS) in header")
            det = _first_key([hdr, primary], ["DETECTOR"])
            exp = _first_key([hdr, primary], ["EXPTIME", "XPOSURE", "EXPOSURE"])
            fixture_wave = _first_key([hdr, primary], ["SDWAVE"])

    bad = ~np.isfinite(image)
    if flags is not None:
        bad |= (flags & int(flag_bits)) != 0
    image[bad] = np.nan
    if var is not None:
        var = np.where(bad | ~np.isfinite(var), np.nan, var)

    return Cutout(
        obs_id=obs_id,
        path=path,
        image=image,
        variance=var,
        wcs=wcs,
        spec_wcs=spec_wcs,
        mjd=float(mjd),
        time_utc=Time(float(mjd), format="mjd", scale="utc").to_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
        detector=_as_detector(det, detector_hint),
        exposure_s=float(exp) if exp is not None else None,
        bad_fraction=float(bad.mean()),
        pixel_scale_arcsec=float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0),
        fixture_wave=float(fixture_wave) if fixture_wave is not None else None,
    )


def _first_key(headers, keys):
    for h in headers:
        for k in keys:
            if k in h and h[k] not in (None, ""):
                return h[k]
    return None


def _as_detector(v, hint) -> int:
    for x in (v, hint):
        try:
            d = int(str(x).strip().lstrip("Dd"))
            if 1 <= d <= 6:
                return d
        except (TypeError, ValueError):
            pass
    raise ValueError("could not determine detector number (1-6)")


def _spectral_wcs(hdr, hdul) -> WCS | None:
    try:
        w = WCS(hdr, fobj=hdul, key="W")
        return w if w.naxis >= 2 else None
    except Exception:
        return None


def target_pixel(c: Cutout, ra: float, dec: float) -> tuple[float, float]:
    x, y = c.wcs.world_to_pixel_values(ra, dec)
    return float(x), float(y)


def inside(c: Cutout, x: float, y: float, margin: float = 0.0) -> bool:
    h, w = c.image.shape
    return margin <= x <= w - 1 - margin and margin <= y <= h - 1 - margin


def wavelength_at(c: Cutout, x: float, y: float) -> tuple[float, float | None]:
    """(wavelength_um, bandpass_um) at pixel (x, y). Wavelength varies across a SPHEREx detector,
    so this must always be evaluated at the pixel of interest, never taken from the header as one number."""
    if c.spec_wcs is None:
        if c.fixture_wave is not None:
            return c.fixture_wave, None
        raise ValueError("no spectral (wavelength) WCS in this cutout")
    out = c.spec_wcs.pixel_to_world(x, y)
    wave, band = (out[0], out[1] if len(out) > 1 else None) if isinstance(out, (tuple, list)) else (out, None)

    def to_um(q):
        if q is None:
            return None
        return float(np.asarray(q.to_value(u.um) if hasattr(q, "to_value") else q).ravel()[0])

    w = to_um(wave)
    if w is None or not np.isfinite(w) or not (0.5 < w < 6.0):
        raise ValueError(f"implausible wavelength {w} um from spectral WCS")
    return w, to_um(band)
