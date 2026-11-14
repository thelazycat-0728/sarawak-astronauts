"""Happy paths for every Track B endpoint, served by the mock provider."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import BAND_RANGES_UM, EpochsResponse, LightcurveResponse
from tests.conftest import BARNARD, FRONTEND_MOCK, png_info


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_lists_every_route(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/health", "/epochs", "/frame/{id}.png", "/diff", "/lightcurve"):
        assert path in paths


def test_epochs_defaults_and_shape(client):
    resp = client.get("/epochs", params=BARNARD)
    assert resp.status_code == 200
    doc = EpochsResponse.model_validate(resp.json())
    assert doc.synthetic is True
    assert doc.target.ra == BARNARD["ra"] and doc.target.dec == BARNARD["dec"]
    assert doc.target.name == "Barnard's Star"
    assert doc.fov_arcmin == 10.0
    assert doc.band.id == "D2"
    assert doc.count == len(doc.epochs) == 9
    assert [e.mjd for e in doc.epochs] == sorted(e.mjd for e in doc.epochs)
    assert doc.diff_url_template is not None
    assert len(doc.diffs) == 1


def test_epochs_echoes_position_fov_and_band(client):
    resp = client.get(
        "/epochs", params={"ra": 10.5, "dec": -45.25, "fov": 20, "band": "D5"}
    )
    assert resp.status_code == 200
    doc = EpochsResponse.model_validate(resp.json())
    assert (doc.target.ra, doc.target.dec, doc.target.name) == (10.5, -45.25, None)
    assert doc.fov_arcmin == 20.0
    assert doc.image.pixel_scale_arcsec * doc.image.width_px == 20 * 60
    assert doc.band.id == "D5"
    lo, hi = BAND_RANGES_UM["D5"]
    for e in doc.epochs:
        assert e.band == "D5" and e.id.endswith("_D5")
        assert lo <= e.wavelength_um <= hi
        assert "band=D5" in e.frame_url and "fov=20" in e.frame_url
        assert "ra=10.5" in e.frame_url and "dec=-45.25" in e.frame_url


def test_all_epoch_urls_are_absolute_pngs(client):
    doc = client.get("/epochs", params=BARNARD).json()
    size = (doc["image"]["width_px"], doc["image"]["height_px"])
    urls = [e["frame_url"] for e in doc["epochs"]] + [d["url"] for d in doc["diffs"]]
    ids = [e["id"] for e in doc["epochs"]]
    tmpl = doc["diff_url_template"]
    urls.append(tmpl.replace("{a}", ids[1]).replace("{b}", ids[4]))
    for url in urls:
        assert url.startswith("http://testserver/"), url
        resp = client.get(url)
        assert resp.status_code == 200, (url, resp.text)
        assert resp.headers["content-type"] == "image/png"
        assert png_info(resp.content) == (size, "L")


def test_urls_follow_the_request_host(app):
    client = TestClient(app, base_url="https://api.example.org")
    doc = client.get("/epochs", params=BARNARD).json()
    assert doc["epochs"][0]["frame_url"].startswith("https://api.example.org/frame/")
    assert doc["diffs"][0]["url"].startswith("https://api.example.org/diff?")
    assert doc["diff_url_template"].startswith(
        "https://api.example.org/diff?a={a}&b={b}&"
    )


def test_frame_url_returns_mock_bytes(client):
    doc = client.get("/epochs", params=BARNARD).json()
    first = doc["epochs"][0]
    resp = client.get(first["frame_url"])
    assert (
        resp.content == (FRONTEND_MOCK / "frames" / f"{first['id']}.png").read_bytes()
    )


def test_frame_custom_stretch_and_no_stretch(client):
    params = {**BARNARD, "fov": 10, "band": "D2"}
    base = client.get("/frame/mock_20250514_01_D2.png", params=params)
    custom = client.get(
        "/frame/mock_20250514_01_D2.png", params={**params, "vmin": 0.0, "vmax": 0.5}
    )
    assert base.status_code == custom.status_code == 200
    assert custom.headers["content-type"] == "image/png"
    assert png_info(custom.content) == ((96, 96), "L")
    assert custom.content != base.content


def test_frame_in_other_band(client):
    params = {**BARNARD, "band": "D6"}
    resp = client.get("/frame/mock_20250514_01_D6.png", params=params)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_diff_prerendered_and_computed(client):
    params = {**BARNARD, "fov": 10, "band": "D2"}
    pair = {"a": "mock_20250514_01_D2", "b": "mock_20260514_09_D2"}
    pre = client.get("/diff", params={**params, **pair})
    swapped = client.get("/diff", params={**params, "a": pair["b"], "b": pair["a"]})
    scaled = client.get("/diff", params={**params, **pair, "scale": 0.5})
    for resp in (pre, swapped, scaled):
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert png_info(resp.content) == ((96, 96), "L")
    assert len({pre.content, swapped.content, scaled.content}) == 3


def test_lightcurve_defaults_and_echo(client):
    resp = client.get("/lightcurve", params={"ra": 76.9564, "dec": 30.4014})
    assert resp.status_code == 200
    doc = LightcurveResponse.model_validate(resp.json())
    assert doc.synthetic is True
    assert (doc.target.ra, doc.target.dec) == (76.9564, 30.4014)
    assert doc.band.id == "D2" and doc.aperture_arcsec == 12.0 and doc.unit == "uJy"
    assert len(doc.points) == 9

    resp = client.get(
        "/lightcurve", params={"ra": 1, "dec": 2, "band": "D3", "aperture": 30}
    )
    doc = LightcurveResponse.model_validate(resp.json())
    assert doc.band.id == "D3" and doc.aperture_arcsec == 30.0
    epochs = client.get("/epochs", params={"ra": 1, "dec": 2, "band": "D3"}).json()
    assert [p.epoch_id for p in doc.points] == [e["id"] for e in epochs["epochs"]]


def test_cors_allows_vite_dev_origin(client):
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
