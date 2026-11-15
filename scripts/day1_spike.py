"""Command-line twin of notebooks/01-irsa-query.ipynb: proves the real-data path through the BACKEND's own code.
    python scripts/day1_spike.py "Barnard's Star"
Query IRSA -> download one cutout -> read it with app.science.fits_io -> print wavelength at the target pixel."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astropy.io import fits  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.science.fits_io import open_cutout, target_pixel, wavelength_at  # noqa: E402
from app.services.irsa import IrsaArchive, build_query, cutout_path  # noqa: E402
from app.services.resolve import DEMO_TARGETS  # noqa: E402
from app.validation import SearchParams  # noqa: E402


def main(name: str) -> None:
    s = get_settings()
    s.ensure_dirs()
    n, ra, dec = DEMO_TARGETS.get(name.lower(), (name, None, None))
    if ra is None:
        sys.exit("Use a demo target name (Barnard's Star, WISE 0855, ...).")
    p = SearchParams(ra, dec, 0.05, detector=2)
    print(f"== {n}  RA={ra} Dec={dec}\n== query:\n{build_query(ra, dec, 2)}\n")
    obs = IrsaArchive(s).search(p)
    print(f"== {len(obs)} observations (detector 2). first 3:")
    for o in obs[:3]:
        print("  ", o)
    if not obs:
        sys.exit("NO-GO: query returned nothing.")
    print("   time_bounds_lower sanity (MJD expected, ~60000-62000):", obs[0].t_mjd)
    o = obs[0]
    path = cutout_path(o, p, s)
    IrsaArchive(s).fetch_cutout(o, p, path)
    print(f"\n== downloaded {path} ({path.stat().st_size / 1e6:.2f} MB)")
    with fits.open(path) as h:
        h.info()
    c = open_cutout(path, o.obs_id, s.flag_mask_bits, o.detector)
    x, y = target_pixel(c, ra, dec)
    w, bw = wavelength_at(c, x, y)
    print(f"\n== read OK: detector={c.detector} time={c.time_utc} scale={c.pixel_scale_arcsec:.2f}\"/px")
    print(f"   target pixel=({x:.1f},{y:.1f})  wavelength at target={w:.4f} um  bandpass={bw}")
    print("GO")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Barnard's Star")
