"""Which frames may be blinked or subtracted together: same detector AND comparable wavelength at the target.
Mirrors frontend/src/lib/groups.ts so group ids match (d{detector}-{lo:.2f})."""
from __future__ import annotations

from typing import Any


def group_frames(frames: list[dict[str, Any]], tol_frac: float) -> list[dict[str, Any]]:
    by_det: dict[int, list[dict]] = {}
    for f in frames:
        by_det.setdefault(int(f["detector"]), []).append(f)

    groups: list[dict[str, Any]] = []
    for det, fs in by_det.items():
        cluster: list[dict] = []

        def flush() -> None:
            nonlocal cluster
            if not cluster:
                return
            lo, hi = cluster[0]["wavelength_um"], cluster[-1]["wavelength_um"]
            groups.append(
                {
                    "group_id": f"d{det}-{lo:.2f}",
                    "label": f"Detector {det}, {lo:.2f} um",
                    "detector": det,
                    "wavelength_um_min": lo,
                    "wavelength_um_max": hi,
                    "frame_ids": [c["frame_id"] for c in cluster],
                }
            )
            cluster = []

        for f in sorted(fs, key=lambda x: x["wavelength_um"]):
            if cluster and (f["wavelength_um"] - cluster[0]["wavelength_um"]) / cluster[0]["wavelength_um"] > tol_frac:
                flush()
            cluster.append(f)
        flush()
    return sorted(groups, key=lambda g: -len(g["frame_ids"]))


def spread(items: list, k: int) -> list:
    """Up to k items, evenly spaced across the list, always keeping the first and last (so the time baseline is kept)."""
    n = len(items)
    if k >= n:
        return list(items)
    if k <= 0:
        return []
    if k == 1:
        return [items[0]]
    idx = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in idx]


def choose_frames(frames: list[dict[str, Any]], max_frames: int, tol_frac: float) -> list[dict[str, Any]]:
    """Which frames to KEEP when more are readable than the display cap.
    A wavelength changes from exposure to exposure at a fixed sky position, so the oldest N frames are usually N
    different wavelengths: nothing comparable, no difference image. Instead fill from the groups with the most
    comparable epochs first, spread across time to keep the baseline."""
    if len(frames) <= max_frames:
        return sorted(frames, key=lambda f: f["observation_time_utc"])
    by_id = {f["frame_id"]: f for f in frames}
    keep: list[dict[str, Any]] = []
    for g in sorted(group_frames(frames, tol_frac), key=lambda g: (-len(g["frame_ids"]), g["group_id"])):
        room = max_frames - len(keep)
        if room <= 0:
            break
        members = sorted((by_id[i] for i in g["frame_ids"]), key=lambda f: f["observation_time_utc"])
        keep += spread(members, room)
    return sorted(keep, key=lambda f: f["observation_time_utc"])
