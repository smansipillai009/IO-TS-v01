"""End-to-end through the real HTTP layer with a synthetic archive. Needs: fastapi, httpx, astropy, reproject.
Run:  SKYDETECTIVE_NO_AUTOAPP=1 pytest -q"""
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
    s = Settings(data_dir=tmp_path / "data")
    fx = make_fixture(tmp_path / "fx")
    return TestClient(create_app(s, FixtureArchive(fx, s)))


def wait(client, rid, stretch="asinh", tries=60):
    for _ in range(tries):
        m = client.get(f"/api/manifest/{rid}", params={"stretch": stretch}).json()
        if m["status"] in ("complete", "failed"):
            return m
        time.sleep(0.5)
    raise AssertionError("timed out")


def test_health(client):
    assert client.get("/api/health").json()["status"] == "healthy"


@pytest.mark.parametrize("q", ["ra=400&dec=0&radius=0.05", "ra=10&dec=95&radius=0.05", "ra=10&dec=0&radius=0.9", "ra=abc&dec=0"])
def test_invalid_input_gives_readable_error(client, q):
    r = client.get(f"/api/search?{q}")
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_full_flow(client):
    r = client.get("/api/search", params={"ra": RA, "dec": DEC, "radius": 0.05}).json()
    rid = r["request_id"]
    assert client.get("/api/search", params={"ra": RA, "dec": DEC, "radius": 0.05}).json()["request_id"] == rid  # idempotent
    m = wait(client, rid)
    assert m["status"] == "complete", m.get("error")
    assert len(m["frames"]) == 4 and m["grid"]["width"] > 32
    gids = {tuple(g["frame_ids"]) for g in m["groups"]}
    assert len(m["groups"]) == 2 and any(len(g) == 3 for g in gids)  # 3 comparable epochs + 1 at another wavelength
    assert len(m["difference_frames"]) == 2  # consecutive pairs of the 3-epoch group only
    for d in m["difference_frames"]:
        a, b = d["based_on"]
        assert next(g for g in m["groups"] if a in g["frame_ids"]) is next(g for g in m["groups"] if b in g["frame_ids"])
    f = m["frames"][0]
    assert f["alignment_status"] in ("good", "degraded", "failed", "unknown") and "partial_coverage" not in f["quality_flags"]
    png = client.get(f["png_url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    # stretch switch changes the URL, not the data
    assert "/linear/" in wait(client, rid, "linear")["frames"][0]["png_url"]
    # combine: add within a group works, across groups is refused
    grp = next(g for g in m["groups"] if len(g["frame_ids"]) == 3)["frame_ids"]
    ok = client.post("/api/jobs/combine", json={"request_id": rid, "op": "median", "frames": grp})
    assert ok.status_code == 200 and client.get(ok.json()["png_url"]).status_code == 200
    other = next(g for g in m["groups"] if len(g["frame_ids"]) == 1)["frame_ids"]
    bad = client.post("/api/jobs/combine", json={"request_id": rid, "op": "add", "frames": [grp[0], other[0]]})
    assert bad.status_code == 422 and "wavelength" in bad.json()["detail"]
    # spectrum at the field centre: one point per usable cutout, each with its own wavelength and time
    sp = client.get("/api/spectrum", params={"request_id": rid, "ra": RA, "dec": DEC, "aperture_arcsec": 12}).json()
    assert len(sp["points"]) == 4 and {round(p["wavelength_um"], 2) for p in sp["points"]} >= {1.28, 2.0}


def test_product_route_is_not_a_file_server(client):
    for path in ["/api/products/../../etc/passwd", "/api/products/0123456789abcdef/asinh/..%2F..%2Fx.png",
                 "/api/products/0123456789abcdef/asinh/notpng.txt", "/api/products/zzzz/asinh/a.png"]:
        assert client.get(path).status_code in (404, 422)


def test_unknown_request_is_404(client):
    assert client.get("/api/manifest/0123456789abcdef").status_code == 404
