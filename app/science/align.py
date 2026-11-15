"""Alignment QA. The WCS reprojection does the alignment; this measures whether it worked.
Method: high-pass both images, normalized cross-correlation, parabolic sub-pixel refinement of the peak.
A residual shift of the FIELD STARS between frames of the same group means the WCS (or the reprojection) is off.
LIMITATIONS: this is a global-shift check (not per-star), and a real proper-motion star can bias it slightly.
Upgrade path: centroid Gaia stars in each frame and report the RMS offset."""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


def _prep(a: np.ndarray) -> np.ndarray:
    x = np.where(np.isfinite(a), a, 0.0).astype(np.float64)
    x = x - ndi.gaussian_filter(x, 8)  # remove smooth background, keep point sources
    x = ndi.gaussian_filter(x, 1.0)  # smooth so the correlation peak is broad enough for sub-pixel fitting
    win = np.outer(np.hanning(x.shape[0]), np.hanning(x.shape[1]))
    return x * win


def measure_shift(ref: np.ndarray, img: np.ndarray) -> tuple[float, float, float]:
    """Returns (dy, dx, ncc_peak). The sign convention is arbitrary; use the magnitude.
    ncc_peak is the normalized correlation at the best shift (about 1 = same structure, near 0 = unrelated)."""
    a, b = _prep(ref), _prep(img)
    norm = float(np.sqrt((a**2).sum() * (b**2).sum()))
    if norm == 0:
        return 0.0, 0.0, 0.0
    corr = np.fft.fftshift(np.real(np.fft.ifft2(np.fft.fft2(a) * np.conj(np.fft.fft2(b))))) / norm
    cy, cx = np.array(corr.shape) // 2
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)

    def refine(c_m, c_0, c_p):
        d = c_m - 2 * c_0 + c_p
        return 0.0 if d == 0 else 0.5 * (c_m - c_p) / d

    dy = dx = 0.0
    if 0 < iy < corr.shape[0] - 1:
        dy = refine(corr[iy - 1, ix], corr[iy, ix], corr[iy + 1, ix])
    if 0 < ix < corr.shape[1] - 1:
        dx = refine(corr[iy, ix - 1], corr[iy, ix], corr[iy, ix + 1])
    return float(iy - cy + dy), float(ix - cx + dx), float(corr[iy, ix])


def alignment_status(
    ref: np.ndarray, img: np.ndarray, good_px: float, degraded_px: float, min_ncc: float = 0.3
) -> tuple[str, float | None]:
    """Returns (status, residual_px). 'unknown' when there is too little shared structure to measure."""
    if np.isfinite(ref).sum() < 100 or np.isfinite(img).sum() < 100:
        return "unknown", None
    dy, dx, ncc = measure_shift(ref, img)
    if ncc < min_ncc:
        return "unknown", None
    r = float(np.hypot(dy, dx))
    return ("good" if r <= good_px else "degraded" if r <= degraded_px else "failed"), r
