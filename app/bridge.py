"""Pipeline result -> the investigation contract the frontend adapter reads (src/lib/adapter.ts RawInvestigation).
No web-framework imports on purpose: this is plain data in, plain data out, so it is easy to test.

Rules: nothing is invented. A fact the pipeline did not measure is omitted (the UI shows 'not reported'), and difference
images are always 'not_validated' because nothing here validates them scientifically."""
from __future__ import annotations

from typing import Any

# DB stage text (set by services/jobs.py and science/pipeline.py progress callbacks) -> frontend job state
STAGE_TO_JOB = {
    "queued": "queued",
    "Starting": "processing",
    "Finding observations": "finding_observations",
    "Downloading cutouts": "downloading",
    "Aligning frames": "processing",
    "Rendering images": "processing",
    "Done": "processing",
}


def live_status(row: dict[str, Any]) -> str:
    if row["status"] == "complete":
        return "complete"
    if row["status"] == "failed":
        return "failed"
    return STAGE_TO_JOB.get(row.get("stage") or "queued", "processing")


def data_mode(row: dict[str, Any]) -> str:
    """'development_fixture' only for the synthetic test archive; recorded real files and IRSA are archive data."""
    releases = {o.get("release") for o in row.get("observations") or []}
    return "development_fixture" if "fixture" in releases else "archive_data"


def _warnings(m: dict[str, Any]) -> list[str]:
    proc = m.get("processing") or {}
    out: list[str] = ["Potential changes only: a difference image is evidence to inspect, not a discovery."]
    sel = proc.get("selection") or {}
    if sel.get("readable", 0) > sel.get("shown", 0):
        out.append(
            f"Showing {sel['shown']} of {sel['readable']} readable observations: the ones with the most comparable "
            "wavelengths, spread across the time range."
        )
    skipped = proc.get("skipped") or []
    if skipped:
        out.append(f"{len(skipped)} observation(s) could not be used (for example: {skipped[0].get('reason', 'unreadable')}).")
    for f in m.get("frames") or []:
        if f.get("alignment_status") in ("degraded", "failed"):
            out.append(f"Alignment of {f['frame_id']} is {f['alignment_status']}; residual shifts can look like motion.")
    out += [x for x in proc.get("limitations") or []]
    return out


def to_investigation(row: dict[str, Any]) -> dict[str, Any]:
    rid = row["id"]
    status = live_status(row)
    base: dict[str, Any] = {"investigation_id": rid, "status": status}
    if status == "failed":
        return {**base, "error": row.get("error") or "Processing failed. Please retry.",
                "error_code": row.get("error_code") or "processing_failed"}
    m = row.get("manifest")
    if status != "complete" or not m:
        return base

    frames = []
    for f in m["frames"]:
        item = {
            "id": f["frame_id"],
            "image_url": f"/products/{rid}/{f['frame_id']}.png",
            "observation_time": f.get("observation_time_utc"),
            "wavelength_um": f.get("wavelength_um"),
            "observation_id": f.get("observation_id"),
            "detector": f.get("detector"),
            "quality_flags": f.get("quality_flags"),
            "alignment_status": f.get("alignment_status"),
        }
        if f.get("exposure_s") is not None:
            item["exposure_s"] = f["exposure_s"]
        frames.append(item)

    mode = data_mode(row)
    proc = m.get("processing") or {}
    return {
        **base,
        "data_mode": mode,
        "frames": frames,
        "difference": None,
        "difference_frames": [
            {"frame_id": d["frame_id"], "based_on": d["based_on"],
             "png_url": f"/products/{rid}/{d['frame_id']}.png", "validation_status": "not_validated"}
            for d in m.get("difference_frames") or []
        ],
        "warnings": _warnings(m),
        "source_archive": "NASA/IPAC IRSA (SPHEREx)" if mode == "archive_data" else "Local development fixture",
        "grid": m.get("grid"),
        "groups": m.get("groups"),
        "processing": {k: proc.get(k) for k in ("alignment_method", "background_method", "data_release", "pipeline_version") if proc.get(k)},
    }
