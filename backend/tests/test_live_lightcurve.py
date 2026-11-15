"""T-009: live ``/lightcurve`` (DATA_MODE=irsa), offline from the recorded SIA
and cutout fixtures: the response shape, ids shared with ``/epochs``, the
saturated target, a measured field star, ``aperture`` validation, and 503, 504
and 502 from the build path it shares with ``/epochs``."""

from __future__ import annotations

import threading
import time

import pytest
import requests

from app.errors import UpstreamError
from app.models import LightcurveResponse, ViewQuery
from app.pipeline import photometry
from app.providers.irsa import LIGHTCURVE_FOV_ARCMIN
from tests.conftest import BARNARD, assert_error
from tests.cutout_helpers import StreamResponse, StreamSession
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes
from tests.test_irsa_provider import VIEW, GatedSession, _busy, _wait_for, live
from tests.test_photometry import FIELD_STAR

STAR = {"ra": FIELD_STAR[0], "dec": FIELD_STAR[1]}


def _lc_in_thread(client, params):
    out: dict = {}
    th = threading.Thread(target=lambda: out.setdefault(
        "r", client.get("/lightcurve", params=params)))
    th.start()
    return th, out


def test_saturated_target_points_are_null_and_bad_with_the_epochs_ids(tmp_path):
    t = live(tmp_path)
    resp = t.client.get("/lightcurve", params={**BARNARD, "band": "D2"})
    assert resp.status_code == 200, resp.text
    doc = LightcurveResponse.model_validate(resp.json())
    assert doc.synthetic is False and doc.target.name is None
    assert (doc.target.ra, doc.target.dec) == (BARNARD["ra"], BARNARD["dec"])
    assert doc.unit == "uJy" and doc.aperture_arcsec == 12.0 and doc.band.id == "D2"
    assert "colour" in doc.method and doc.method == photometry.METHOD
    assert len(doc.points) == 3
    assert [p.mjd for p in doc.points] == sorted(p.mjd for p in doc.points)
    # Barnard's Star is saturated in every exposure: no number, honestly.
    assert all((p.flux, p.flux_err, p.quality) == (None, None, "bad")
               for p in doc.points)
    # Same exposures as /epochs at the default fov, from the same cache entry.
    calls = (len(t.sia.calls), len(t.cutouts.calls))
    epochs = t.client.get("/epochs", params={**BARNARD, "band": "D2"}).json()
    assert LIGHTCURVE_FOV_ARCMIN == 10.0 == epochs["fov_arcmin"]
    assert [p.epoch_id for p in doc.points] == [e["id"] for e in epochs["epochs"]]
    for p, e in zip(doc.points, epochs["epochs"], strict=True):
        assert (p.time_utc, p.mjd, p.release, p.wavelength_um) == (
            e["time_utc"], e["mjd"], e["release"], e["wavelength_um"])
    assert (len(t.sia.calls), len(t.cutouts.calls)) == calls == (1, 3)


def test_a_field_star_is_measured_and_any_aperture_reuses_the_cache(tmp_path):
    t = live(tmp_path)
    fluxes = []
    for aperture in (6, 12, 24):
        resp = t.client.get("/lightcurve", params={**STAR, "aperture": aperture})
        assert resp.status_code == 200, resp.text
        doc = LightcurveResponse.model_validate(resp.json())
        assert doc.aperture_arcsec == aperture
        # Every test exposure is the same recorded cutout, so the points agree.
        assert {p.quality for p in doc.points} == {"good"}
        assert len({p.flux for p in doc.points}) == 1
        p = doc.points[0]
        assert p.flux > 10 * p.flux_err > 0
        fluxes.append(p.flux)
    assert fluxes == sorted(fluxes)  # a larger aperture holds more of the PSF
    # At 60" the recorded cutout has a masked pixel inside the aperture.
    wide = t.client.get("/lightcurve", params={**STAR, "aperture": 60}).json()
    assert {(p["flux"], p["quality"]) for p in wide["points"]} == {(None, "bad")}
    assert (len(t.sia.calls), len(t.cutouts.calls)) == (1, 3)  # one build only


def test_lightcurve_after_epochs_at_the_default_fov_is_a_cache_hit(tmp_path):
    t = live(tmp_path)
    assert t.client.get("/epochs", params=VIEW).status_code == 200
    calls = (len(t.sia.calls), len(t.cutouts.calls))
    resp = t.client.get("/lightcurve", params=BARNARD)
    assert resp.status_code == 200 and len(resp.json()["points"]) == 3
    assert (len(t.sia.calls), len(t.cutouts.calls)) == calls


def test_other_fovs_cut_no_stamps_and_the_lightcurve_uses_fov_10(tmp_path):
    t = live(tmp_path)
    assert t.client.get("/epochs", params={**VIEW, "fov": 12}).status_code == 200
    built = t.provider._cache.get(t.provider.cache_key(ViewQuery(**{**VIEW, "fov": 12})))
    assert built is not None and built.stamps == {}
    resp = t.client.get("/lightcurve", params=BARNARD)
    assert resp.status_code == 200 and len(resp.json()["points"]) == 3
    assert len(t.sia.calls) == 2  # a second build, at fov 10


def test_an_unreadable_stamp_is_a_bad_point_not_a_failed_build(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise UpstreamError("IRSA returned an unreadable cutout")

    monkeypatch.setattr(photometry, "extract_stamp", broken)
    t = live(tmp_path)
    resp = t.client.get("/lightcurve", params=STAR)
    assert resp.status_code == 200, resp.text
    assert {(p["flux"], p["quality"]) for p in resp.json()["points"]} == {(None, "bad")}
    assert t.client.get("/epochs", params=VIEW).json()["count"] == 3


@pytest.mark.parametrize("aperture", ["2.9", "60.1", "nan", "inf", "abc", "-12"])
def test_bad_aperture_is_400_without_any_irsa_call(tmp_path, aperture):
    t = live(tmp_path)
    resp = t.client.get("/lightcurve", params={**BARNARD, "aperture": aperture})
    assert_error(resp, 400, "invalid_parameter")
    assert "aperture" in resp.json()["error"]["message"]
    assert t.sia.calls == [] and t.cutouts.calls == []


def test_aperture_limits_are_accepted(tmp_path):
    t = live(tmp_path)
    for aperture in ("3", "60"):
        resp = t.client.get("/lightcurve", params={**STAR, "aperture": aperture})
        assert resp.status_code == 200, resp.text
        assert resp.json()["aperture_arcsec"] == float(aperture)


def test_no_coverage_is_an_empty_200(tmp_path):
    t = live(tmp_path, sia=FakeSession(response=FakeResponse(fixture_bytes("empty.xml"))))
    resp = t.client.get("/lightcurve", params=BARNARD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["points"] == [] and resp.json()["synthetic"] is False


# ------------------------------------------------- shared build path: 503, 504, 502


def test_waiter_cap_answers_503(tmp_path):
    gate = threading.Event()
    sia = GatedSession(gate, response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    t = live(tmp_path, sia=sia, epochs_max_waiters=1)
    th, out = _lc_in_thread(t.client, BARNARD)
    try:
        _wait_for(lambda: t.provider.waiting == 1)
        started = time.monotonic()
        resp = t.client.get("/lightcurve", params={**BARNARD, "ra": 10.0})
        assert time.monotonic() - started < 2
        assert_error(resp, 503, "unavailable")
        assert "preset" in resp.json()["error"]["message"]
    finally:
        gate.set()
        th.join(30)
    assert out["r"].status_code == 200 and t.provider.waiting == 0


def test_deadline_is_504_and_the_build_still_fills_the_cache(tmp_path):
    gate = threading.Event()
    sia = GatedSession(gate, response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    t = live(tmp_path, sia=sia, epochs_deadline_s=0.2)
    try:
        resp = t.client.get("/lightcurve", params=BARNARD)
        assert_error(resp, 504, "upstream_timeout")
        assert "0.2 s" in resp.json()["error"]["message"]
    finally:
        gate.set()
    _wait_for(lambda: len(t.provider._cache) == 1, timeout=60)
    resp = t.client.get("/lightcurve", params=BARNARD)
    assert resp.status_code == 200 and len(resp.json()["points"]) == 3
    assert len(sia.calls) == 1


def test_all_downloads_failing_is_502_and_timing_out_is_504(tmp_path):
    t = live(tmp_path / "a", cutouts=StreamSession(replies=[_busy()]))
    resp = t.client.get("/lightcurve", params=BARNARD)
    assert_error(resp, 502, "upstream_error")
    assert len(t.provider._cache) == 0
    t = live(tmp_path / "b", cutouts=StreamSession(replies=[requests.ReadTimeout()]))
    assert_error(t.client.get("/lightcurve", params=BARNARD), 504, "upstream_timeout")


def test_sia_errors_are_502(tmp_path):
    t = live(tmp_path, sia=FakeSession(error=requests.ConnectionError("down")))
    assert_error(t.client.get("/lightcurve", params=BARNARD), 502, "upstream_error")


def test_a_cutout_that_cannot_be_cleaned_is_502(tmp_path):
    from tests.test_pipeline_failures import with_image_unit

    adu = StreamResponse(with_image_unit("adu"))
    t = live(tmp_path, cutouts=StreamSession(replies=[adu]))
    assert_error(t.client.get("/lightcurve", params=BARNARD), 502, "upstream_error")
