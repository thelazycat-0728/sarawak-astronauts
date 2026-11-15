"""T-007: live ``/diff`` (DATA_MODE=irsa) and ``diff_url_template`` in live
``/epochs``, offline from the recorded SIA and cutout fixtures and from fake
frames: the diff maths, the template, 400/404, the shared render slots (503, no
slot deadlock) and the frame deadline (504)."""

from __future__ import annotations

import io
import threading
import time

import numpy as np
import pytest
from PIL import Image

import app.pipeline
from app.pipeline.clean import CleanFrame
from app.pipeline.diff import diff_frames, diff_png, diff_scale
from tests.conftest import assert_error, png_info
from tests.cutout_helpers import FOV10, StreamSession, ok
from tests.test_irsa_provider import (
    FRAME_A,
    VIEW,
    _blocking_replies,
    _frame_in_thread,
    _wait_for,
    live,
)

EPOCH_A = "sx_dr1_2025W35_1A_0146_4_D2"  # visit 1 of the recorded Barnard query
EPOCH_B = "sx_dr1_2026W12_1A_0311_4_D2"  # visit 2
UNKNOWN = "sx_dr1_2025W35_1A_9999_4_D2"  # well formed, not at this position
PAIR = {"a": EPOCH_A, "b": EPOCH_B}


def decode(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        assert im.mode == "L"
        return np.asarray(im)


def frame(image: np.ndarray, filled: np.ndarray | None = None) -> CleanFrame:
    return CleanFrame(image=image, flagged_fraction=0.0, status="good", flags=[],
                      coverage=1.0, saturated_px=0 if filled is None else 1,
                      filled=filled)


def _diff_in_thread(client, params):
    out: dict = {}

    def go():
        started = time.monotonic()
        out["r"] = client.get("/diff", params={**VIEW, **params})
        out["s"] = time.monotonic() - started

    th = threading.Thread(target=go)
    th.start()
    return th, out


# ------------------------------------------------------------ diff maths


def test_diff_frames_convention_sign_scale_and_no_data():
    a = np.zeros((5, 5))
    b = np.zeros((5, 5))
    a[2, 1] = 4.0  # the source was here in a ...
    b[1, 1] = 4.0  # ... and one pixel north (row 0 = north) in b
    a[4, 4] = np.nan  # no data in a
    b[0, 4] = np.nan  # no data in b
    gray = decode(diff_frames(frame(a), frame(b), scale=4.0)[0])
    assert gray[1, 1] == 255 and gray[2, 1] == 1  # white where brighter in b
    assert gray[4, 4] == 128 and gray[0, 4] == 128  # no data shows as no change
    assert (np.delete(gray.ravel(), [6, 11]) == 128).all()
    # Identical frames: all no change, default scale falls back to 1.
    png, scale = diff_frames(frame(a), frame(a.copy()))
    assert scale == 1.0 and (decode(png) == 128).all()


def test_diff_frames_is_the_preset_renderer_with_a_measurable_scale():
    rng = np.random.default_rng(7)
    a = rng.normal(size=(21, 21))
    b = rng.normal(size=(21, 21))
    # Without filled pixels it is byte-identical to the preset diff_png.
    assert diff_frames(frame(a), frame(b)) == diff_png(a, b)
    # A filled saturated core in b with a huge value: shown, but it does not
    # set the default scale.
    filled = np.zeros(a.shape, dtype=bool)
    filled[10, 10] = True
    b_sat = b.copy()
    b_sat[10, 10] = 1e4
    png, scale = diff_frames(frame(a), frame(b_sat, filled))
    b_measured = b_sat.copy()
    b_measured[10, 10] = np.nan
    assert scale == diff_scale(a, b_measured)  # the filled pixel left out
    assert scale < diff_scale(a, b_sat)
    assert decode(png)[10, 10] == 255  # the filled core still shows
    # A given scale is used as is.
    assert diff_frames(frame(a), frame(b), scale=0.25)[1] == 0.25


# ------------------------------------------------------------ live /epochs template


def test_live_template_builds_working_diff_urls_from_the_warm_cache(tmp_path):
    t = live(tmp_path)
    doc = t.client.get("/epochs", params=VIEW).json()
    tmpl = doc["diff_url_template"]
    assert tmpl == ("http://testserver/diff?a={a}&b={b}&ra=269.4464&dec=4.7668"
                    "&fov=10&band=D2")
    assert doc["diffs"] == []
    first, last = doc["epochs"][0], doc["epochs"][-1]
    calls = (len(t.sia.calls), len(t.cutouts.calls))
    resp = t.client.get(tmpl.replace("{a}", first["id"]).replace("{b}", last["id"]))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    size = (doc["image"]["width_px"], doc["image"]["height_px"])
    assert png_info(resp.content) == (size, "L")  # the frames' size
    # Every recorded cutout holds the same pixels, so b - a is no change.
    assert (decode(resp.content) == 128).all()
    assert (len(t.sia.calls), len(t.cutouts.calls)) == calls  # no IRSA call
    assert (t.provider.frames_rendering, t.provider.frames_waiting) == (0, 0)


def test_live_template_follows_the_request_host(tmp_path):
    from fastapi.testclient import TestClient

    t = live(tmp_path)
    client = TestClient(t.client.app, base_url="https://api.example.org")
    tmpl = client.get("/epochs", params={**VIEW, "fov": 6}).json()["diff_url_template"]
    assert tmpl.startswith("https://api.example.org/diff?a={a}&b={b}&")
    assert tmpl.endswith("ra=269.4464&dec=4.7668&fov=6&band=D2")


def test_cold_diff_downloads_both_frames(tmp_path):
    t = live(tmp_path)
    resp = t.client.get("/diff", params={**VIEW, **PAIR})
    assert resp.status_code == 200, resp.text
    assert png_info(resp.content) == ((97, 97), "L")
    assert len(t.cutouts.calls) == 2  # one cutout per frame
    again = t.client.get("/diff", params={**VIEW, **PAIR})
    assert again.content == resp.content and len(t.cutouts.calls) == 2  # cached


def test_live_diff_sign_scale_and_filled_through_the_provider(tmp_path, monkeypatch):
    side = 9
    base = np.full((side, side), 1.0)
    a, b = base.copy(), base.copy()
    a[5, 4] += 3.0  # the star in a ...
    b[4, 4] += 3.0  # ... one pixel north in b
    a[0, 0] = np.nan
    frames = {EPOCH_A: frame(a), EPOCH_B: frame(b)}
    seen = []

    def fake_load(epoch_id, ra, dec, fov, band, **kwargs):
        seen.append((epoch_id, ra, dec, fov, band))
        return frames[epoch_id]

    monkeypatch.setattr(app.pipeline, "load_frame", fake_load)
    t = live(tmp_path, cutouts=StreamSession())  # any network call fails the test
    gray = decode(t.client.get("/diff", params={**VIEW, **PAIR}).content)
    assert sorted(seen) == sorted([(EPOCH_A, 269.4464, 4.7668, 10.0, "D2"),
                                   (EPOCH_B, 269.4464, 4.7668, 10.0, "D2")])
    assert gray[4, 4] == 255 and gray[5, 4] == 1  # north/south white/black pair
    assert gray[0, 0] == 128 and (gray[[0, 1, 2, 3, 6, 7, 8]] == 128).all()
    swapped = decode(t.client.get("/diff", params={**VIEW, "a": EPOCH_B,
                                                   "b": EPOCH_A}).content)
    assert swapped[4, 4] == 1 and swapped[5, 4] == 255
    half = decode(t.client.get("/diff", params={**VIEW, **PAIR, "scale": 6}).content)
    assert half[4, 4] == 192 and half[5, 4] == 64  # 128 +/- round(127 * 0.5)


# ------------------------------------------------------------ 400 and 404


@pytest.mark.parametrize("extra", [
    {"a": EPOCH_A, "b": EPOCH_A},  # a == b
    {"a": EPOCH_A},  # b missing
    {**PAIR, "scale": 0},
    {**PAIR, "scale": -1},
    {**PAIR, "band": "D2-07"},
    {**PAIR, "fov": 31},
    {**PAIR, "dec": 91},
    {"a": "bad.id", "b": EPOCH_B},
])
def test_bad_live_diff_params_are_400_without_any_call(tmp_path, extra):
    t = live(tmp_path, cutouts=StreamSession())
    resp = t.client.get("/diff", params={**VIEW, **extra})
    assert_error(resp, 400, "invalid_parameter")
    assert t.sia.calls == [] and t.cutouts.calls == []
    assert t.provider.frames_rendering == 0


def test_a_equals_b_message_names_the_problem(tmp_path):
    t = live(tmp_path)
    resp = t.client.get("/diff", params={**VIEW, "a": EPOCH_A, "b": EPOCH_A})
    assert "different epochs" in resp.json()["error"]["message"]


def test_unknown_or_other_band_id_is_404_and_remembered(tmp_path):
    now = [1000.0]
    t = live(tmp_path, clock=lambda: now[0])
    resp = t.client.get("/diff", params={**VIEW, "a": EPOCH_A, "b": UNKNOWN})
    assert_error(resp, 404, "not_found")
    _wait_for(lambda: t.provider.frames_rendering == 0, timeout=30)  # a's slot freed
    sia_calls = len(t.sia.calls)
    t.provider._query_cache.clear()
    # Remembered for this position and band: no render, no IRSA query.
    resp = t.client.get("/diff", params={**VIEW, "a": UNKNOWN, "b": EPOCH_B})
    assert_error(resp, 404, "not_found")
    assert len(t.sia.calls) == sia_calls and t.provider.frames_rendering == 0
    # The same id is remembered for /frame as well (one shared memory).
    assert_error(t.client.get(f"/frame/{UNKNOWN}.png", params=VIEW), 404, "not_found")
    assert len(t.sia.calls) == sia_calls
    other_band = "sx_dr1_2025W34_2B_0090_4_D3"
    assert_error(t.client.get("/diff", params={**VIEW, "a": EPOCH_A, "b": other_band}),
                 404, "not_found")
    _wait_for(lambda: t.provider.frames_rendering == 0, timeout=30)


# ------------------------------------------------------------ slots and deadline


def test_diff_gets_503_when_two_slots_never_free_and_holds_none_meanwhile(tmp_path):
    release = threading.Event()
    cutouts = StreamSession(replies=[_blocking_replies({"0146_4": release})])
    t = live(tmp_path, cutouts=cutouts, frame_max_concurrent=2, frame_slot_wait_s=0.3)
    th, out = _frame_in_thread(t.client, FRAME_A)  # holds 1 of the 2 slots
    try:
        _wait_for(lambda: t.provider.frames_rendering == 1)
        th_d, out_d = _diff_in_thread(t.client, PAIR)
        _wait_for(lambda: t.provider.frames_waiting == 1)
        # One slot is free, but the diff does not take it while it waits.
        assert t.provider.frames_rendering == 1
        th_d.join(10)
        assert_error(out_d["r"], 503, "unavailable")
        assert 0.25 <= out_d["s"] < 2
        assert "preset" in out_d["r"].json()["error"]["message"]
        # The free slot was never taken, so a /frame still gets it at once.
        resp = t.client.get(f"/frame/{EPOCH_B}.png", params=VIEW)
        assert resp.status_code == 200
    finally:
        release.set()
        th.join(30)
    assert out["r"].status_code == 200
    _wait_for(lambda: t.provider.frames_rendering == 0)
    assert t.client.get("/diff", params={**VIEW, **PAIR}).status_code == 200


def test_two_diffs_on_two_slots_never_deadlock(tmp_path):
    release = threading.Event()
    cutouts = StreamSession(replies=[_blocking_replies({"0146_4": release,
                                                        "0311_4": release})])
    t = live(tmp_path, cutouts=cutouts, frame_max_concurrent=2, frame_slot_wait_s=20.0)
    th1, out1 = _diff_in_thread(t.client, PAIR)
    th2 = None
    try:
        _wait_for(lambda: t.provider.frames_rendering == 2)  # diff 1 has both slots
        th2, out2 = _diff_in_thread(t.client, {**PAIR, "fov": 11})
        _wait_for(lambda: t.provider.frames_waiting == 1)
        assert t.provider.frames_rendering == 2  # diff 2 holds nothing while waiting
    finally:
        release.set()
        th1.join(30)
        if th2 is not None:
            th2.join(30)
    assert out1["r"].status_code == 200, out1["r"].text
    assert out2["r"].status_code == 200, out2["r"].text
    assert png_info(out2["r"].content)[0] == (107, 107)  # fov 11
    assert (t.provider.frames_rendering, t.provider.frames_waiting) == (0, 0)


def test_one_slot_renders_both_frames_in_turn(tmp_path):
    t = live(tmp_path, cutouts=StreamSession(replies=[ok(FOV10)]), frame_max_concurrent=1)
    resp = t.client.get("/diff", params={**VIEW, **PAIR})
    assert resp.status_code == 200, resp.text
    assert len(t.cutouts.calls) == 2 and t.cutouts.max_active == 1
    assert t.provider.frames_rendering == 0


def test_diff_deadline_returns_504_and_the_renders_fill_the_cache(tmp_path):
    gate = threading.Event()
    cutouts = StreamSession(replies=[ok(FOV10)], gate=gate)
    t = live(tmp_path, cutouts=cutouts, frame_deadline_s=0.3)
    try:
        started = time.monotonic()
        resp = t.client.get("/diff", params={**VIEW, **PAIR})
        assert time.monotonic() - started < 5
        assert_error(resp, 504, "upstream_timeout")
        message = resp.json()["error"]["message"]
        assert "0.3 s" in message and "difference" in message and "preset" in message
        assert t.provider.frames_rendering == 2  # both renders keep their slots
    finally:
        gate.set()
    _wait_for(lambda: t.provider.frames_rendering == 0, timeout=60)
    resp = t.client.get("/diff", params={**VIEW, **PAIR})
    assert resp.status_code == 200 and len(cutouts.calls) == 2  # retry is cached
