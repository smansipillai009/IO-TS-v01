"""Live mode end to end through HTTP: POST coordinates -> poll -> frames/groups/difference + PNGs.
Synthetic FITS (tests/make_fixture.py). Needs fastapi, httpx, astropy, reproject.  Run: SKYDETECTIVE_NO_AUTOAPP=1 pytest -q"""
import os
import time

os.environ["SKYDETECTIVE_NO_AUTOAPP"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.irsa import FixtureArchive
from tests.make_fixture import DEC, RA, make_fixture


@pytest.fixture()
def client(tmp_path):
    s = Settings(data_dir=tmp_path / "data", demo_dir=tmp_path / "none", investigation_mode="live")
    return TestClient(create_app(s, FixtureArchive(make_fixture(tmp_path / "fx"), s)))


def poll(c, iid, tries=80):
    seen = []
    for _ in range(tries):
        r = c.get(f"/api/investigations/{iid}").json()
        seen.append(r["status"])
        if r["status"] in ("complete", "failed"):
            return r, seen
        time.sleep(0.5)
    raise AssertionError(f"timed out, saw {seen}")


BODY = {"ra_deg": RA, "dec_deg": DEC, "fov_deg": 0.1, "max_frames": 6}


def test_post_runs_the_real_pipeline_for_these_coordinates(client):
    r = client.post("/api/investigations", json=BODY).json()
    assert r["coordinates_used"] is True and len(r["investigation_id"]) == 16
    assert client.post("/api/investigations", json=BODY).json()["investigation_id"] == r["investigation_id"]  # idempotent
    res, seen = poll(client, r["investigation_id"])
    assert res["status"] == "complete", res.get("error")
    assert res["data_mode"] == "development_fixture"  # synthetic test archive is labelled as such
    assert len(res["frames"]) == 4 and len(res["groups"]) == 2 and len(res["difference_frames"]) == 2
    assert all(f["observation_time"] and f["wavelength_um"] for f in res["frames"])
    png = client.get(res["frames"][0]["image_url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(res["difference_frames"][0]["png_url"]).status_code == 200
    assert res["difference_frames"][0]["validation_status"] == "not_validated"


def test_max_frames_keeps_the_comparable_trio(client):
    body = {**BODY, "max_frames": 3}
    res, _ = poll(client, client.post("/api/investigations", json=body).json()["investigation_id"])
    assert res["status"] == "complete" and len(res["frames"]) == 3
    assert len(res["groups"]) == 1 and len(res["difference_frames"]) == 2  # the 3 epochs at one wavelength


def test_oversized_field_is_a_readable_422(client):
    r = client.post("/api/investigations", json={**BODY, "fov_deg": 1.5})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_spectrum_and_catalog_use_the_investigation_id(client):
    iid = client.post("/api/investigations", json=BODY).json()["investigation_id"]
    poll(client, iid)
    sp = client.get("/api/spectrum", params={"request_id": iid, "ra": RA, "dec": DEC, "aperture_arcsec": 12}).json()
    assert len(sp["points"]) == 4
