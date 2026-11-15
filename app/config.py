"""All tunables in one place. Environment variables override defaults (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")).resolve())
    # Processed products served under /products/<investigation_id>/ (static mode and the stored demo field).
    demo_dir: Path = field(default_factory=lambda: Path(os.getenv("DEMO_PRODUCTS_DIR", "./products")).resolve())
    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o]
    )
    max_radius_deg: float = _f("MAX_RADIUS_DEG", 0.5)
    max_frames: int = _i("MAX_FRAMES", 24)
    # How many candidate cutouts may be downloaded to FIND comparable frames (display cap is max_frames).
    max_pool: int = _i("MAX_POOL", 40)
    # "live" = POST /api/investigations runs the real pipeline; "static" = serve the one stored demo field only.
    investigation_mode: str = os.getenv("INVESTIGATION_MODE", "live")
    max_est_mb: float = _f("MAX_EST_MB", 150)
    irsa_sia_url: str = os.getenv("IRSA_SIA_URL", "https://irsa.ipac.caltech.edu/SIA")
    irsa_sia_collection: str = os.getenv("IRSA_SIA_COLLECTION", "spherex_qr")
    irsa_tap_url: str = os.getenv("IRSA_TAP_URL", "https://irsa.ipac.caltech.edu/TAP")
    # Appended to an observation's uri to request a cutout (IRSA cutout service; note the 'd' after Dec).
    cutout_query: str = os.getenv("CUTOUT_QUERY", "center={ra},{dec}d&size={size_deg}")
    data_release: str = os.getenv("DATA_RELEASE", "spherex_qr")
    pipeline_version: str = os.getenv("PIPELINE_VERSION", "0.1.0")
    wavelength_tol_frac: float = _f("WAVELENGTH_TOL_FRAC", 0.02)
    align_good_px: float = _f("ALIGN_GOOD_PX", 0.5)
    align_degraded_px: float = _f("ALIGN_DEGRADED_PX", 1.5)
    flag_mask_bits: int = _i("FLAG_MASK_BITS", 0xFFFFFFFF)
    workers: int = _i("WORKERS", 2)
    # SPHEREx native pixel scale (arcsec/pixel). VERIFY against the FITS header on Day 1.
    pixel_scale_arcsec: float = 6.15
    max_grid_px: int = 600
    http_timeout_s: float = 60.0
    http_retries: int = 4

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sky.sqlite"

    @property
    def cutout_dir(self) -> Path:
        return self.data_dir / "cutouts"

    @property
    def product_dir(self) -> Path:
        return self.data_dir / "products"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.cutout_dir, self.product_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
