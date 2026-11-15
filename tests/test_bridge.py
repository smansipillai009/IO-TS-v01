"""Pure logic: frame selection and the pipeline -> investigation-contract bridge (no web framework, no FITS)."""
from app.bridge import data_mode, live_status, to_investigation
from app.science.grouping import choose_frames, spread


def fr(i, det, wl, t):
    return dict(frame_id=f"f{i}", detector=det, wavelength_um=wl, observation_time_utc=t)


def test_spread_keeps_first_and_last():
    assert spread(list(range(10)), 3)[0] == 0 and spread(list(range(10)), 3)[-1] == 9
    s = spread(list(range(100)), 5)
    assert s[0] == 0 and s[-1] == 99 and len(s) == 5 and s == sorted(s)
    assert spread([1, 2, 3], 10) == [1, 2, 3] and spread([1, 2, 3], 0) == []


def test_choose_frames_prefers_comparable_groups_over_oldest():
    # 6 oldest frames are 6 different wavelengths; a later trio shares one wavelength (the useful one)
    old = [fr(i, 2, 1.0 + 0.3 * i, f"2025-07-0{i + 1}T00:00:00Z") for i in range(6)]
    trio = [fr(10 + i, 2, 2.50, f"2026-0{i + 1}-01T00:00:00Z") for i in range(3)]
    kept = choose_frames(old + trio, 4, 0.02)
    ids = {f["frame_id"] for f in kept}
    assert {"f10", "f11", "f12"} <= ids and len(kept) == 4  # the comparable trio is always kept
    assert [f["observation_time_utc"] for f in kept] == sorted(f["observation_time_utc"] for f in kept)


def test_choose_frames_noop_when_under_cap():
    fs = [fr(1, 2, 1.0, "2025-01-01T00:00:00Z"), fr(0, 2, 1.0, "2024-01-01T00:00:00Z")]
    assert [f["frame_id"] for f in choose_frames(fs, 6, 0.02)] == ["f0", "f1"]


def test_status_mapping():
    assert live_status({"status": "pending", "stage": "queued"}) == "queued"
    assert live_status({"status": "processing", "stage": "Finding observations"}) == "finding_observations"
    assert live_status({"status": "processing", "stage": "Downloading cutouts"}) == "downloading"
    assert live_status({"status": "processing", "stage": "Rendering images"}) == "processing"
    assert live_status({"status": "complete", "stage": "Done"}) == "complete"
    assert live_status({"status": "failed", "stage": "x"}) == "failed"


MANIFEST = {
    "frames": [
        {"frame_id": "d2_a", "observation_id": "a", "observation_time_utc": "2025-07-12T00:00:00Z", "wavelength_um": 1.28,
         "detector": 2, "exposure_s": 112.6, "quality_flags": [], "alignment_status": "good"},
        {"frame_id": "d2_b", "observation_id": "b", "observation_time_utc": "2026-01-12T00:00:00Z", "wavelength_um": 1.285,
         "detector": 2, "exposure_s": None, "quality_flags": ["partial_coverage"], "alignment_status": "degraded"},
    ],
    "difference_frames": [{"frame_id": "diff_d2_a__d2_b", "based_on": ["d2_a", "d2_b"], "png_url": "x"}],
    "groups": [{"group_id": "d2-1.28", "label": "Detector 2, 1.28 um", "detector": 2, "wavelength_um_min": 1.28,
                "wavelength_um_max": 1.285, "frame_ids": ["d2_a", "d2_b"]}],
    "grid": {"width": 59, "height": 59, "wcs": {"crval": [269.4, 4.6], "crpix": [30, 30], "cdelt": [-0.0017, 0.0017]}},
    "processing": {"alignment_method": "m", "background_method": "b", "data_release": "r", "pipeline_version": "0.1.0",
                   "selection": {"readable": 9, "shown": 2}, "skipped": [{"obs_id": "z", "reason": "no wavelength"}],
                   "limitations": ["L1"]},
}


def row(release="spherex_qr", status="complete", stage="Done"):
    return {"id": "0123456789abcdef", "status": status, "stage": stage, "manifest": MANIFEST, "error": None,
            "observations": [{"release": release}]}


def test_complete_result_matches_what_the_frontend_adapter_reads():
    r = to_investigation(row())
    assert r["investigation_id"] == "0123456789abcdef" and r["status"] == "complete" and r["data_mode"] == "archive_data"
    f0, f1 = r["frames"]
    assert f0["image_url"] == "/products/0123456789abcdef/d2_a.png" and f0["observation_time"].startswith("2025")
    assert f0["exposure_s"] == 112.6 and "exposure_s" not in f1            # unknown is omitted, never invented
    assert f1["quality_flags"] == ["partial_coverage"] and f1["alignment_status"] == "degraded"
    d = r["difference_frames"][0]
    assert d["png_url"].endswith("/diff_d2_a__d2_b.png") and d["validation_status"] == "not_validated"
    assert d["based_on"] == ["d2_a", "d2_b"] and r["difference"] is None
    assert r["grid"]["width"] == 59 and r["groups"][0]["group_id"] == "d2-1.28"


def test_warnings_are_honest():
    w = to_investigation(row())["warnings"]
    assert any("not a discovery" in x for x in w)
    assert any("2 of 9" in x for x in w) and any("could not be used" in x for x in w)
    assert any("d2_b is degraded" in x for x in w) and "L1" in w


def test_synthetic_fixture_is_labelled_as_development_data():
    assert data_mode(row(release="fixture")) == "development_fixture"
    assert data_mode(row(release="local-real")) == "archive_data"


def test_failed_and_pending_shapes():
    f = to_investigation({"id": "x", "status": "failed", "stage": "", "error": "No SPHEREx observations cover this sky position.",
                          "error_code": "no_observations", "observations": [], "manifest": None})
    assert f == {"investigation_id": "x", "status": "failed", "error": "No SPHEREx observations cover this sky position.",
                 "error_code": "no_observations"}
    p = to_investigation({"id": "x", "status": "processing", "stage": "Downloading cutouts", "observations": [], "manifest": None})
    assert p == {"investigation_id": "x", "status": "downloading"}
