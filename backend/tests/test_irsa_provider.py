"""DATA_MODE=irsa: live /epochs and /frame from recorded IRSA and cutout fixtures
(offline), plus the deadline, the /epochs cache and the IRSA concurrency cap."""

from __future__ import annotations

import inspect
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from app.config import Settings
from app.cutouts import fetch_cutouts
from app.cutouts.limit import LimitedSiaSession
from app.main import create_app
from app.models import EpochsResponse
from app.providers import make_provider
from app.providers.irsa import IrsaProvider, TtlCache
from app.providers.mock import MockProvider
from tests.conftest import BARNARD, assert_error, png_info
from tests.cutout_helpers import (
    FOV10,
    StreamResponse,
    StreamSession,
    fixture_exposure,
    make_settings,
    ok,
)
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes

BACKEND = Path(__file__).resolve().parents[1]
VIEW = {**BARNARD, "fov": 10, "band": "D2"}


class GatedSession(FakeSession):
    """A FakeSession whose calls wait until ``gate`` is set."""

    def __init__(self, gate: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self.gate = gate

    def get(self, url, *, params=None, timeout=None, headers=None):
        assert self.gate.wait(10), "test gate never opened"
        return super().get(url, params=params, timeout=timeout, headers=headers)


def live(tmp_path, sia=None, cutouts=None, clock=time.monotonic, **changes):
    settings = make_settings(tmp_path, data_mode="irsa", **changes)
    sia = sia or FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    cutouts = cutouts or StreamSession(replies=[ok(FOV10)])
    provider = IrsaProvider(settings, sia_session=sia, cutout_session=cutouts, clock=clock)
    app = create_app(settings=settings, provider=provider)
    return SimpleNamespace(settings=settings, sia=sia, cutouts=cutouts, provider=provider,
                           client=TestClient(app, raise_server_exceptions=False))


def test_epochs_is_one_cleaned_exposure_per_visit(tmp_path):
    t = live(tmp_path)
    resp = t.client.get("/epochs", params=VIEW)
    assert resp.status_code == 200, resp.text
    doc = EpochsResponse.model_validate(resp.json())
    assert doc.synthetic is False
    assert doc.count == 3 and [e.visit for e in doc.epochs] == [1, 2, 3]
    assert len({e.visit for e in doc.epochs}) == doc.count  # one per visit
    assert [e.mjd for e in doc.epochs] == sorted(e.mjd for e in doc.epochs)
    assert doc.diff_url_template is None and doc.diffs == []
    assert (doc.image.width_px, doc.image.height_px) == (97, 97)
    assert doc.stretch.shared and doc.stretch.vmax > doc.stretch.vmin
    assert doc.target.ra == BARNARD["ra"] and doc.target.dec == BARNARD["dec"]
    assert all(e.id.startswith("sx_") and e.id.endswith("_D2") for e in doc.epochs)
    assert len(t.sia.calls) == 1  # one SIA query
    assert len(t.cutouts.calls) == 3  # one cutout per visit out of 61 exposures


def test_every_frame_url_is_absolute_and_serves_a_png(tmp_path):
    t = live(tmp_path)
    doc = t.client.get("/epochs", params=VIEW).json()
    vmin, vmax = doc["stretch"]["vmin"], doc["stretch"]["vmax"]
    sia_calls, cutout_calls = len(t.sia.calls), len(t.cutouts.calls)
    for e in doc["epochs"]:
        url = e["frame_url"]
        assert url.startswith(f"http://testserver/frame/{e['id']}.png?"), url
        assert "ra=269.4464&dec=4.7668&fov=10&band=D2" in url
        assert f"&vmin={vmin!r}&vmax={vmax!r}" in url
        resp = t.client.get(url)
        assert resp.status_code == 200, (url, resp.text)
        assert resp.headers["content-type"] == "image/png"
        assert png_info(resp.content) == ((97, 97), "L")
    # Frames come from the cutout disk cache: no IRSA call at all.
    assert (len(t.sia.calls), len(t.cutouts.calls)) == (sia_calls, cutout_calls)


def test_frame_urls_follow_the_request_host(tmp_path):
    t = live(tmp_path)
    client = TestClient(t.client.app, base_url="https://api.example.org")
    doc = client.get("/epochs", params=VIEW).json()
    assert all(e["frame_url"].startswith("https://api.example.org/frame/")
               for e in doc["epochs"])


def test_unknown_frame_id_is_404(tmp_path):
    t = live(tmp_path)
    assert_error(t.client.get("/frame/not_an_epoch.png", params=VIEW), 404, "not_found")
    other_band = "sx_dr1_2025W34_2B_0090_4_D3"
    assert_error(t.client.get(f"/frame/{other_band}.png", params=VIEW), 404, "not_found")


def test_diff_and_lightcurve_are_unavailable(tmp_path):
    t = live(tmp_path)
    resp = t.client.get("/diff", params={**VIEW, "a": "sx_a_D2", "b": "sx_b_D2"})
    assert_error(resp, 503, "unavailable")
    assert_error(t.client.get("/lightcurve", params=BARNARD), 503, "unavailable")
    # Parameters are still validated first.
    assert_error(t.client.get("/lightcurve", params={"ra": 1, "dec": 99}), 400,
                 "invalid_parameter")


def test_deadline_returns_504_and_the_build_still_fills_the_cache(tmp_path):
    gate = threading.Event()
    sia = GatedSession(gate, response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    t = live(tmp_path, sia=sia, epochs_deadline_s=0.2)
    try:
        started = time.monotonic()
        resp = t.client.get("/epochs", params=VIEW)
        assert time.monotonic() - started < 5
        assert_error(resp, 504, "upstream_timeout")
        assert "0.2 s" in resp.json()["error"]["message"]
        assert "preset" in resp.json()["error"]["message"]
    finally:
        gate.set()
    # Retries join the same background build (single flight) until it is done.
    for _ in range(100):
        resp = t.client.get("/epochs", params=VIEW)
        if resp.status_code == 200:
            break
        assert_error(resp, 504, "upstream_timeout")
    assert resp.status_code == 200 and resp.json()["count"] == 3
    assert len(sia.calls) == 1 and len(t.cutouts.calls) == 3


def test_epochs_cache_hit_and_expiry(tmp_path):
    now = [1000.0]
    t = live(tmp_path, clock=lambda: now[0], epochs_cache_ttl_s=600.0)
    first = t.client.get("/epochs", params=VIEW)
    second = t.client.get("/epochs", params=VIEW)
    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(t.sia.calls) == 1 and len(t.cutouts.calls) == 3  # second was a hit
    # A position that rounds to the same 4 decimals is a hit and is echoed.
    near = t.client.get("/epochs", params={**VIEW, "ra": 269.44641})
    assert near.json()["target"]["ra"] == 269.44641
    assert "ra=269.44641&" in near.json()["epochs"][0]["frame_url"]
    assert len(t.sia.calls) == 1
    now[0] += 601.0
    t.client.get("/epochs", params=VIEW)
    assert len(t.sia.calls) == 2  # expired: queried again


def test_upstream_errors_are_502_and_not_cached(tmp_path):
    sia = FakeSession(error=requests.ConnectionError("down"))
    t = live(tmp_path, sia=sia)
    assert_error(t.client.get("/epochs", params=VIEW), 502, "upstream_error")
    assert_error(t.client.get("/epochs", params=VIEW), 502, "upstream_error")
    assert len(sia.calls) == 2
    assert len(t.provider._cache) == 0


def test_no_coverage_is_an_empty_200_and_is_cached(tmp_path):
    t = live(tmp_path, sia=FakeSession(response=FakeResponse(fixture_bytes("empty.xml"))))
    for _ in range(2):
        resp = t.client.get("/epochs", params=VIEW)
        assert resp.status_code == 200, resp.text
        doc = EpochsResponse.model_validate(resp.json())
        assert doc.count == 0 and doc.epochs == [] and doc.synthetic is False
    assert len(t.sia.calls) == 1 and t.cutouts.calls == []


def test_ttl_cache_caps_entries():
    cache = TtlCache(ttl_s=10.0, max_entries=2, clock=lambda: 0.0)
    for k in "abc":
        cache.put(k, k)
    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (None, "b", "c")


# --------------------------------------------------------- IRSA concurrency cap


def test_cutout_downloads_share_the_process_wide_cap(tmp_path):
    settings = make_settings(tmp_path, cutout_workers=6, irsa_max_concurrency=2)
    session = StreamSession(replies=[ok(FOV10)], delay_s=0.1)
    items = [fixture_exposure(FOV10, obs_id=f"obs_{i}",
                              access_url=f"https://irsa.ipac.caltech.edu/ibe/x/obs_{i}.fits")
             for i in range(6)]
    batch = fetch_cutouts(items, FOV10["ra"], FOV10["dec"], 10, settings=settings,
                          session=session)
    assert batch.downloads == 6
    assert session.max_active == 2


def test_sia_queries_take_a_slot_from_the_same_cap(tmp_path):
    settings = Settings(irsa_max_concurrency=3)
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    class Slow:
        def get(self, url, *, params, timeout, headers):
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return FakeResponse(b"x")

    session = LimitedSiaSession(settings, Slow())
    threads = [threading.Thread(target=session.get, args=("u",),
                                kwargs={"params": [], "timeout": 1, "headers": {}})
               for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert state["max"] == 3


# ------------------------------------------------------- mode selection, imports


def test_mode_selection():
    assert isinstance(make_provider(Settings()), MockProvider)
    assert isinstance(make_provider(Settings(data_mode="irsa")), IrsaProvider)
    with pytest.raises(ValueError, match="unknown DATA_MODE 'dr2'"):
        make_provider(Settings(data_mode="dr2"))


def test_mock_mode_is_the_default_and_unchanged(monkeypatch):
    monkeypatch.delenv("DATA_MODE", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    health = client.get("/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["x-data-mode"] == "mock"
    doc = EpochsResponse.model_validate(client.get("/epochs", params=BARNARD).json())
    assert doc.synthetic is True and doc.count == 9 and len(doc.diffs) == 1
    assert doc.diff_url_template is not None
    assert client.get("/lightcurve", params=BARNARD).status_code == 200


def test_health_reports_irsa_mode(tmp_path):
    resp = live(tmp_path).client.get("/health")
    assert resp.json() == {"status": "ok"} and resp.headers["x-data-mode"] == "irsa"


_PROBE = (
    "import sys\n"
    "from fastapi.testclient import TestClient\n"
    "import app.main\n"
    "c = TestClient(app.main.app)\n"
    "assert c.get('/health').status_code == 200\n"
    "if app.main.app.state.provider.name == 'mock':\n"
    "    assert c.get('/epochs', params={'ra': 1, 'dec': 2}).status_code == 200\n"
    "heavy = sorted(m for m in sys.modules\n"
    "               if m.split('.')[0] in ('reproject', 'dask') or m == 'app.pipeline')\n"
    "print(app.main.app.state.provider.name, heavy)\n"
)


@pytest.mark.parametrize("mode", [None, "irsa"])
def test_startup_does_not_import_the_pipeline(tmp_path, mode):
    env = {k: v for k, v in os.environ.items() if k != "DATA_MODE"}
    env["CUTOUT_CACHE_DIR"] = str(tmp_path / "cache")
    if mode:
        env["DATA_MODE"] = mode
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=BACKEND, env=env, check=False,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"{mode or 'mock'} []"


# ------------------------------------------------- fix-1: failures are not "no data"


def _busy():
    return StreamResponse(b"Server busy", status_code=503, headers={"Retry-After": "0"})


def test_all_downloads_failing_is_502_not_cached_and_recovers(tmp_path):
    irsa_up = [False]

    def reply(url):
        return ok(FOV10) if irsa_up[0] else _busy()

    t = live(tmp_path, cutouts=StreamSession(replies=[reply]))
    resp = t.client.get("/epochs", params=VIEW)
    assert_error(resp, 502, "upstream_error")
    assert "preset" in resp.json()["error"]["message"]
    # 3 visits x 3 candidates x (1 + 2 retries), and nothing was cached.
    assert len(t.cutouts.calls) == 27 and len(t.provider._cache) == 0
    irsa_up[0] = True
    resp = t.client.get("/epochs", params=VIEW)
    assert resp.status_code == 200 and resp.json()["count"] == 3
    assert len(t.sia.calls) == 2  # rebuilt, not served from the cache


def test_all_downloads_timing_out_is_504(tmp_path):
    t = live(tmp_path, cutouts=StreamSession(replies=[requests.ReadTimeout()]))
    assert_error(t.client.get("/epochs", params=VIEW), 504, "upstream_timeout")
    assert len(t.provider._cache) == 0


def test_partial_sequence_is_cached_only_briefly(tmp_path):
    now = [1000.0]
    visit2_up = [False]

    def reply(url):  # visit 2 holds the 2026W11 and 2026W12 exposures
        visit2 = "level2_2026W11_" in url or "level2_2026W12_" in url
        return _busy() if visit2 and not visit2_up[0] else ok(FOV10)

    t = live(tmp_path, cutouts=StreamSession(replies=[reply]), clock=lambda: now[0])
    assert t.settings.epochs_cache_degraded_ttl_s == 30.0
    doc = t.client.get("/epochs", params=VIEW).json()
    assert [e["visit"] for e in doc["epochs"]] == [1, 3]
    now[0] += 29.0
    assert t.client.get("/epochs", params=VIEW).json() == doc  # still a hit
    assert len(t.sia.calls) == 1
    visit2_up[0] = True
    now[0] += 2.0  # past the 30 s degraded TTL
    doc = t.client.get("/epochs", params=VIEW).json()
    assert [e["visit"] for e in doc["epochs"]] == [1, 2, 3] and len(t.sia.calls) == 2
    now[0] += 60.0  # a full sequence keeps the normal TTL
    t.client.get("/epochs", params=VIEW)
    assert len(t.sia.calls) == 2


# ------------------------------------------------- fix-1: waiters and deadlines


def _get_in_thread(client, params):
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", client.get("/epochs",
                                                                         params=params)))
    th.start()
    return th, out


def _wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition never became true"
        time.sleep(0.01)


def test_waiter_cap_answers_503_at_once(tmp_path):
    gate = threading.Event()
    sia = GatedSession(gate, response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    t = live(tmp_path, sia=sia, epochs_max_waiters=1)
    th, out = _get_in_thread(t.client, VIEW)
    try:
        _wait_for(lambda: t.provider.waiting == 1)
        started = time.monotonic()
        resp = t.client.get("/epochs", params={**VIEW, "ra": 10.0})
        assert time.monotonic() - started < 2
        assert_error(resp, 503, "unavailable")
        assert "preset" in resp.json()["error"]["message"]
    finally:
        gate.set()
        th.join(30)
    assert out["r"].status_code == 200 and t.provider.waiting == 0


def test_frame_deadline_returns_504(tmp_path):
    gate = threading.Event()
    cutouts = StreamSession(replies=[ok(FOV10)], gate=gate)
    t = live(tmp_path, cutouts=cutouts, frame_deadline_s=0.3)
    try:
        started = time.monotonic()
        resp = t.client.get("/frame/sx_dr1_2025W35_1A_0146_4_D2.png", params=VIEW)
        assert time.monotonic() - started < 5
        assert_error(resp, 504, "upstream_timeout")
        assert "0.3 s" in resp.json()["error"]["message"]
    finally:
        gate.set()
    # The render finished in the background, so the retry is a cache hit.
    _wait_for(lambda: any((tmp_path / "cache").glob("*.fits")))
    for _ in range(50):
        resp = t.client.get("/frame/sx_dr1_2025W35_1A_0146_4_D2.png", params=VIEW)
        if resp.status_code == 200:
            break
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"


def test_second_waiter_keeps_a_queued_build_alive(tmp_path):
    t = live(tmp_path, epochs_deadline_s=4.0)
    slots = t.provider._build_slots
    slots.acquire()
    slots.acquire()  # both build slots busy: the next build is queued
    released = False
    try:
        th_a, out_a = _get_in_thread(t.client, VIEW)
        _wait_for(lambda: t.provider.waiting == 1)
        time.sleep(2.0)
        th_b, out_b = _get_in_thread(t.client, VIEW)
        _wait_for(lambda: t.provider.waiting == 2)
        th_a.join(10)
        assert_error(out_a["r"], 504, "upstream_timeout")
        (future,) = t.provider._inflight.values()
        assert not future.cancelled()  # B still waits on it
        slots.release()
        slots.release()
        released = True
        th_b.join(30)
    finally:
        if not released:
            slots.release()
            slots.release()
    assert out_b["r"].status_code == 200, out_b["r"].text
    assert out_b["r"].json()["count"] == 3


def test_last_waiter_cancels_a_queued_build(tmp_path):
    t = live(tmp_path, epochs_deadline_s=0.2)
    slots = t.provider._build_slots
    slots.acquire()
    slots.acquire()
    try:
        assert_error(t.client.get("/epochs", params=VIEW), 504, "upstream_timeout")
        assert t.provider._inflight == {} and t.provider.waiting == 0
    finally:
        slots.release()
        slots.release()
    time.sleep(0.2)
    assert t.sia.calls == []  # the cancelled build never ran


class _Abort(BaseException):
    pass


def test_base_exception_in_a_build_does_not_stick_in_flight(tmp_path):
    t = live(tmp_path)
    calls = []

    def boom(q):
        calls.append(q)
        raise _Abort()

    t.provider._build = boom
    for _ in range(2):
        assert_error(t.client.get("/epochs", params=VIEW), 500, "internal_error")
        assert t.provider.waiting == 0
        _wait_for(lambda: t.provider._inflight == {})
    assert len(calls) == 2  # the second request started a new build


# ------------------------------------------------- fix-1: health, CORS, logging


def test_health_is_async_and_cors_exposes_the_data_mode(client):
    from app.routers import health

    assert inspect.iscoroutinefunction(health.health)
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.json() == {"status": "ok"}
    exposed = resp.headers["access-control-expose-headers"].lower()
    assert "x-data-mode" in [h.strip() for h in exposed.split(",")]


def test_irsa_mode_logs_app_info(tmp_path):
    live(tmp_path)
    logger = logging.getLogger("app")
    assert logger.getEffectiveLevel() <= logging.INFO
    names = [h.get_name() for h in logger.handlers]
    assert names.count("app-irsa-info") == 1
    live(tmp_path)  # a second app adds no second handler
    assert [h.get_name() for h in logger.handlers].count("app-irsa-info") == 1
