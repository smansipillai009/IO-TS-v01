"""User-driven add / subtract / median of already-aligned frames (POST /api/jobs/combine).
Cheap: it reads aligned.npz written by the pipeline, so there is no archive access and no reprojection."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from ..errors import NotFound, ValidationFailed
from .render import save_diff_png, save_gray_png

OPS = ("add", "subtract", "median")


def combine(rid: str, op: str, frame_ids: list[str], manifest: dict, product_dir: Path, stretch: str) -> dict:
    if op not in OPS:
        raise ValidationFailed(f"op must be one of {', '.join(OPS)}.")
    if not 2 <= len(frame_ids) <= 24 or len(set(frame_ids)) != len(frame_ids):
        raise ValidationFailed("Choose between 2 and 24 different frames.")
    if op == "subtract" and len(frame_ids) != 2:
        raise ValidationFailed("Subtract needs exactly two frames: earlier first, later second.")
    frames = {f["frame_id"]: f for f in manifest["frames"]}
    if any(i not in frames for i in frame_ids):
        raise NotFound("One of those frames is not part of this request.")
    group_of = {i: g["group_id"] for g in manifest["groups"] for i in g["frame_ids"]}
    if len({group_of[i] for i in frame_ids}) != 1:
        raise ValidationFailed("The selected observations are not wavelength-compatible. Choose frames from one group.")
    npz = product_dir / rid / "aligned.npz"
    if not npz.exists():
        raise NotFound("Processed frames are no longer available. Please re-run the search.")
    with np.load(npz) as z:
        arrs = [z[i] for i in frame_ids]
    if op == "subtract":
        e, l = sorted(frame_ids, key=lambda i: frames[i]["observation_time_utc"])
        with np.load(npz) as z:
            res = z[l] - z[e]
        kind = "diff"
    elif op == "add":
        res, kind = np.where(np.all(np.isfinite(arrs), axis=0), np.sum(arrs, axis=0), np.nan), "gray"
    else:
        res, kind = np.nanmedian(np.stack(arrs), axis=0), "gray"
    cid = "c_" + hashlib.sha1(f"{op}|{'|'.join(sorted(frame_ids))}".encode()).hexdigest()[:12]
    path = product_dir / rid / "combine" / stretch / f"{cid}.png"
    if kind == "diff":
        save_diff_png(res, path)
    else:
        save_gray_png(res, stretch, path)
    return {
        "frame_id": cid,
        "op": op,
        "based_on": frame_ids,
        "png_url": f"/api/products/{rid}/combine/{stretch}/{cid}.png",
        "note": "Sum requires every frame to be valid at a pixel; median ignores masked pixels. No photometric scaling applied.",
    }
