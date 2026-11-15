"""The agreed investigation contract in STATIC mode (one stored field). Uses tiny synthetic PNGs written by the test (not SPHEREx data)."""
import json
import os

os.environ["SKYDETECTIVE_NO_AUTOAPP"] = "1"

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app

MANIFEST = {
    "investigation_id": "demo-001",
    "status": "complete",
    "data_mode": "archive_data",
    "frames": [
        {"id": "frame-001", "image_url": "/products/demo-001/frame_001.png", "observation_time": "2025-12-01T00:00:00Z",
         "wavelength_um": 1.28, "observation_id": "obs_a", "quality_flags": ["partial_coverage"]},
        {"id": "frame-002", "image_url": "/products/demo-001/frame_002.png", "observation_time": None,
         "wavelength_um": 1.29, "observation_id": "obs_b"},
    ],
    "difference": None,
    "warnings": ["Frame 1 has partial coverage.", "No validated difference product."],
}


def build(tmp_path, manifest=MANIFEST, files=("frame_001.png", "frame_002.png")):
    d = tmp_path / "products" / "demo-001"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for f in files:
        Image.new("L", (8, 8), 7).save(d / f)
    s = Settings(data_dir=tmp_path / "data", demo_dir=tmp_path / "products", investigation_mode="static")
    return TestClient(create_app(s, archive=object()))


GOOD = {"ra_deg": 269.452, "dec_deg": 4.693, "fov_deg": 0.1, "max_frames": 6}


def test_post_then_get_returns_manifest_untouched(tmp_path):
    c = build(tmp_path)
    r = c.post("/api/investigations", json=GOOD)
    assert r.status_code == 200
    body = r.json()
    assert body["investigation_id"] == "demo-001" and body["status"] == "queued"
    assert body["coordinates_used"] is False  # honest: live processing is not connected
    got = c.get("/api/investigations/demo-001").json()
    assert got == MANIFEST  # every field and every warning preserved
    assert got["difference"] is None


def test_images_are_served(tmp_path):
    c = build(tmp_path)
    r = c.get("/products/demo-001/frame_001.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG" and r.headers["content-type"] == "image/png"


@pytest.mark.parametrize("path", ["/products/demo-001/manifest.json", "/products/demo-001/..%2Fx.png", "/products/demo-001/nope.png",
                                  "/products/DEMO/frame_001.png", "/products/..%2F/frame_001.png"])
def test_product_route_is_not_a_file_server(tmp_path, path):
    assert build(tmp_path).get(path).status_code in (404, 422)


@pytest.mark.parametrize("patch", [{"ra_deg": 400}, {"dec_deg": 95}, {"fov_deg": 0}, {"fov_deg": 1.5}, {"max_frames": 0}, {"max_frames": 99}])
def test_invalid_requests_get_readable_errors(tmp_path, patch):
    r = build(tmp_path).post("/api/investigations", json={**GOOD, **patch})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and r.json()["detail"]


def test_missing_body_field_is_readable(tmp_path):
    r = build(tmp_path).post("/api/investigations", json={"ra_deg": 1})
    assert r.status_code == 422 and "Invalid request" in r.json()["detail"]


def test_missing_image_fails_loudly_but_keeps_warnings(tmp_path):
    c = build(tmp_path, files=("frame_001.png",))
    got = c.get("/api/investigations/demo-001").json()
    assert got["status"] == "failed" and got["error_code"] == "products_missing" and "frame_002.png" in got["error"]
    assert got["warnings"] == MANIFEST["warnings"]


def test_unknown_and_malformed_ids(tmp_path):
    c = build(tmp_path)
    assert c.get("/api/investigations/other-1").status_code == 404
    assert c.get("/api/investigations/..%2Fetc").status_code in (404, 422)
    assert c.get("/api/investigations/BAD_ID").status_code == 422


def test_no_products_installed_is_503_not_fake_data(tmp_path):
    s = Settings(data_dir=tmp_path / "data", demo_dir=tmp_path / "empty", investigation_mode="static")
    c = TestClient(create_app(s, archive=object()))
    r = c.post("/api/investigations", json=GOOD)
    assert r.status_code == 503 and "No processed products" in r.json()["detail"]
