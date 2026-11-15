"""Array -> PNG. Display stretch only; never alters the science arrays.
Orientation contract with the frontend (src/lib/wcs.ts): north up, east left, FITS origin at the bottom-left,
so the array is flipped vertically on write."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

STRETCHES = ("linear", "asinh", "zscale")


def _robust_sigma(x: np.ndarray) -> float:
    med = np.median(x)
    return float(1.4826 * np.median(np.abs(x - med)))


def stretch_to_uint8(a: np.ndarray, kind: str) -> np.ndarray:
    """NaN (masked or uncovered) pixels become 0. Returns uint8."""
    out = np.zeros(a.shape, dtype=np.uint8)
    ok = np.isfinite(a)
    if ok.sum() < 4:
        return out
    v = a[ok]
    if kind == "zscale":
        # zscale-like: robust clip around the median (sky background level)
        med, sig = float(np.median(v)), max(_robust_sigma(v), 1e-12)
        lo, hi = med - 2.0 * sig, med + 8.0 * sig
        x = np.clip((a - lo) / (hi - lo), 0, 1)
    else:
        lo, hi = np.percentile(v, [0.5, 99.5])
        if hi <= lo:
            hi = lo + 1e-12
        x = np.clip((a - lo) / (hi - lo), 0, 1)
        if kind == "asinh":
            k = 10.0
            x = np.arcsinh(k * x) / np.arcsinh(k)
        elif kind != "linear":
            raise ValueError(f"unknown stretch {kind}")
    out[ok] = np.round(x[ok] * 255).astype(np.uint8)
    return out


def diverging_rgb(d: np.ndarray, nsigma: float = 5.0) -> np.ndarray:
    """Later minus earlier. Red = brighter now, blue = fainter now, white = no change, masked = transparent via alpha."""
    ok = np.isfinite(d)
    rgba = np.zeros(d.shape + (4,), dtype=np.uint8)
    if ok.sum() < 4:
        return rgba
    sig = max(_robust_sigma(d[ok]), 1e-12)
    t = np.clip(np.where(ok, d, 0) / (nsigma * sig), -1, 1)
    pos, neg = np.clip(t, 0, 1), np.clip(-t, 0, 1)
    rgba[..., 0] = np.round(255 * (1 - neg * 0.75)).astype(np.uint8)
    rgba[..., 1] = np.round(255 * (1 - np.maximum(pos, neg))).astype(np.uint8)
    rgba[..., 2] = np.round(255 * (1 - pos * 0.75)).astype(np.uint8)
    rgba[..., 3] = np.where(ok, 255, 0).astype(np.uint8)
    return rgba


def save_gray_png(a: np.ndarray, kind: str, path: Path) -> None:
    img = Image.fromarray(np.flipud(stretch_to_uint8(a, kind)), mode="L")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)


def save_diff_png(d: np.ndarray, path: Path) -> None:
    img = Image.fromarray(np.flipud(diverging_rgb(d)), mode="RGBA")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
