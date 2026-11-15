"""cutouts -> masked -> background-subtracted -> reprojected to ONE shared grid -> grouped -> QA -> PNGs + manifest.

Scientific rules enforced here (see scientific_processing_execution_plan.txt):
  * only frames in the same group (same detector, comparable wavelength AT THE TARGET) are ever subtracted
  * flagged / non-finite pixels are masked before reprojection, so they can never become a 'difference'
  * alignment is validated (align.py) and reported per frame; a failed frame is never differenced
  * every parameter is recorded in the manifest so a result can be reproduced
Nothing here says 'discovery'. A difference image is evidence to inspect, not a detection."""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Callable

import numpy as np
from astropy.wcs import WCS

from ..config import Settings
from ..services.irsa import ArchiveClient, Observation, cutout_path
from ..validation import SearchParams
from .align import alignment_status
from .fits_io import Cutout, inside, open_cutout, target_pixel, wavelength_at
from .grouping import choose_frames, group_frames
from .render import STRETCHES, save_diff_png, save_gray_png

log = logging.getLogger("skydetective.pipeline")
Progress = Callable[[str, float], None]
MIN_COVERAGE = 0.5

LIMITATIONS = [
    "Frames are only compared within one detector and one wavelength group.",
    "No photometric scaling is applied before differencing; calibration differences can leave residuals.",
    "Alignment is checked with a global cross-correlation, not per-star centroids.",
    "Quality flags are masked as a group; individual flag bits are not yet decoded.",
    "A residual in a difference image is not a discovery. Three or more usable epochs are needed for a candidate.",
]


def safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:60]


def make_grid(p: SearchParams, s: Settings) -> tuple[WCS, tuple[int, int], dict]:
    field_arcsec = 2 * p.radius * 3600
    scale = max(s.pixel_scale_arcsec, field_arcsec / s.max_grid_px)
    n = max(int(math.ceil(field_arcsec / scale)), 32)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [p.ra, p.dec]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    w.wcs.cdelt = [-scale / 3600, scale / 3600]
    info = {"width": n, "height": n, "wcs": {"crval": [p.ra, p.dec], "crpix": [(n + 1) / 2] * 2, "cdelt": list(w.wcs.cdelt)}}
    return w, (n, n), info


def _software_versions() -> dict:
    import astropy, numpy, reproject

    return {"astropy": astropy.__version__, "numpy": numpy.__version__, "reproject": reproject.__version__}


def run_pipeline(
    rid: str,
    p: SearchParams,
    observations: list[Observation],
    s: Settings,
    archive: ArchiveClient,
    progress: Progress,
) -> dict:
    from reproject import reproject_interp

    out_dir = s.product_dir / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    grid_wcs, shape, grid_info = make_grid(p, s)
    skipped: list[dict] = []

    # 1. download + read -------------------------------------------------------------------------
    frames: list[dict] = []
    cuts: dict[str, Cutout] = {}
    for i, obs in enumerate(observations):
        progress("Downloading cutouts", 0.05 + 0.35 * i / max(len(observations), 1))
        try:
            path = cutout_path(obs, p, s)
            if not path.exists():
                archive.fetch_cutout(obs, p, path)
            c = open_cutout(path, obs.obs_id, s.flag_mask_bits, obs.detector)
            x, y = target_pixel(c, p.ra, p.dec)
            if not inside(c, x, y):
                raise ValueError("target position is not on this cutout")
            wave, _ = wavelength_at(c, x, y)
        except Exception as e:
            log.warning("skipping %s: %s", obs.obs_id, e)
            skipped.append({"obs_id": obs.obs_id, "reason": str(e)[:200]})
            continue
        fid = f"d{c.detector}_{safe_id(obs.obs_id)}"
        frames.append(
            {
                "frame_id": fid,
                "observation_id": obs.obs_id,
                "observation_time_utc": c.time_utc,
                "wavelength_um": round(wave, 4),
                "detector": c.detector,
                "exposure_s": c.exposure_s,
                "quality_flags": [],
                "alignment_status": "unknown",
            }
        )
        cuts[fid] = c
    if not frames:
        raise ValueError("None of the observations could be read. See 'skipped' for the reasons.")

    # 1b. keep the most comparable frames (see grouping.choose_frames) --------------------------------
    n_readable = len(frames)
    frames = choose_frames(frames, p.max_frames or s.max_frames, s.wavelength_tol_frac)
    cuts = {f["frame_id"]: cuts[f["frame_id"]] for f in frames}

    # 2. reproject to the shared grid + background ------------------------------------------------
    aligned: dict[str, np.ndarray] = {}
    for i, f in enumerate(frames):
        progress("Aligning frames", 0.4 + 0.3 * i / len(frames))
        c = cuts[f["frame_id"]]
        arr, _ = reproject_interp((c.image, c.wcs), grid_wcs, shape_out=shape, order="bilinear")
        arr = arr.astype(np.float32)
        finite = np.isfinite(arr)
        f["coverage_fraction"] = round(float(finite.mean()), 3)
        if finite.any():
            arr = arr - np.float32(np.nanmedian(arr))  # per-frame background: median of the unmasked field
        flags = f["quality_flags"]
        if f["coverage_fraction"] < 0.9:
            flags.append("partial_coverage")
        if c.bad_fraction > 0.2:
            flags.append("many_flagged_pixels")
        aligned[f["frame_id"]] = arr
    frames = [f for f in frames if f["coverage_fraction"] > 0]
    if not frames:
        raise ValueError("No frame overlaps the requested field.")

    # 3. groups + alignment QA --------------------------------------------------------------------
    groups = group_frames(frames, s.wavelength_tol_frac)
    by_id = {f["frame_id"]: f for f in frames}
    alignment_reference: dict[str, str] = {}
    for g in groups:
        members = sorted((by_id[i] for i in g["frame_ids"]), key=lambda f: f["observation_time_utc"])
        ref = max(members, key=lambda f: (f["coverage_fraction"], -members.index(f)))
        alignment_reference[g["group_id"]] = ref["frame_id"]
        measured = []
        for f in members:
            if f is ref:
                continue
            st, res = alignment_status(aligned[ref["frame_id"]], aligned[f["frame_id"]], s.align_good_px, s.align_degraded_px)
            f["alignment_status"], f["alignment_residual_px"] = st, None if res is None else round(res, 3)
            measured.append(st)
        # The reference is only 'good' if everything measured against it was good; otherwise we cannot say which side is off.
        ref["alignment_status"] = "good" if measured and all(m == "good" for m in measured) else "unknown"

    # 4. difference frames (same group, consecutive epochs) ---------------------------------------
    diffs: list[dict] = []
    diff_arrays: dict[str, np.ndarray] = {}
    for g in groups:
        members = sorted((by_id[i] for i in g["frame_ids"]), key=lambda f: f["observation_time_utc"])
        for a, b in zip(members, members[1:]):
            if min(a["coverage_fraction"], b["coverage_fraction"]) < MIN_COVERAGE:
                continue
            if "failed" in (a["alignment_status"], b["alignment_status"]):
                continue
            did = f"diff_{a['frame_id']}__{b['frame_id']}"
            diff_arrays[did] = aligned[b["frame_id"]] - aligned[a["frame_id"]]  # later minus earlier; NaN where either is masked
            diffs.append({"frame_id": did, "based_on": [a["frame_id"], b["frame_id"]], "png_url": f"/api/products/{rid}/{{stretch}}/{did}.png"})

    # 5. persist arrays + render PNGs (one set per stretch; the client only adjusts brightness/contrast) ---
    np.savez_compressed(out_dir / "aligned.npz", **aligned)
    for k, st in enumerate(STRETCHES):
        progress("Rendering images", 0.7 + 0.28 * k / len(STRETCHES))
        for fid, arr in aligned.items():
            save_gray_png(arr, st, out_dir / st / f"{fid}.png")
        for did, d in diff_arrays.items():
            save_diff_png(d, out_dir / st / f"{did}.png")

    for f in frames:
        f["png_url"] = f"/api/products/{rid}/{{stretch}}/{f['frame_id']}.png"
    frames.sort(key=lambda f: f["observation_time_utc"])

    return {
        "request_id": rid,
        "status": "complete",
        "grid": grid_info,
        "groups": groups,
        "frames": frames,
        "difference_frames": diffs,
        "processing": {
            "alignment_method": "wcs_reprojection (reproject_interp, bilinear) + cross-correlation residual check",
            "alignment_reference": alignment_reference,
            "background_method": "per-frame median of unmasked pixels",
            "flux_scaling": "none",
            "mask": f"FLAGS & 0x{s.flag_mask_bits:X}, non-finite pixels",
            "stretch": "{stretch}",
            "data_release": s.data_release,
            "pipeline_version": s.pipeline_version,
            "software_versions": _software_versions(),
            "png_orientation": "north up, east left, FITS origin bottom-left",
            "n_observations_requested": len(observations),
            "skipped": skipped,
            "selection": {"readable": n_readable, "shown": len(frames),
                          "rule": "fill from the wavelength groups with the most epochs first, spread across time"},
            "limitations": LIMITATIONS,
        },
    }
