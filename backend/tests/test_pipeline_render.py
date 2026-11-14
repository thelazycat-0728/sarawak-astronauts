"""Shared stretch, PNG output and the stateless render_frame (offline)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.cutouts import QueryCache
from app.errors import InvalidParameter, NotFound, UpstreamError
from app.pipeline.render import compute_stretch, encode_png, render_frame, to_gray8
from tests.cutout_helpers import FOV10, StreamResponse, StreamSession, make_settings, ok
from tests.irsa_helpers import FakeResponse, FakeSession, fixture_bytes
from tests.pipeline_helpers import DEC, RA

S = Settings()
EPOCH = FOV10["epoch_id"]  # sx_dr1_2025W34_2B_0090_4_D2, in the barnard_d2 SIA fixture


def decode(png: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(png))
    assert image.mode == "L" and image.format == "PNG"
    return np.asarray(image)


# ------------------------------------------------------------------ stretch


def test_stretch_uses_percentiles_of_all_frames():
    a = np.linspace(0.0, 1.0, 1001)
    b = np.linspace(1.0, 3.0, 1001)
    vmin, vmax = compute_stretch([a, b], S)
    pixels = np.concatenate([a, b])
    assert vmin == pytest.approx(np.percentile(pixels, 1.0), rel=1e-4)
    assert vmax == pytest.approx(np.percentile(pixels, 99.5), rel=1e-4)
    assert compute_stretch([a], S) != (vmin, vmax)  # really shared, not per frame


def test_stretch_ignores_invalid_pixels_and_is_rounded():
    a = np.array([np.nan, 0.123456789, 9.87654321, np.nan])
    vmin, vmax = compute_stretch([a], S)
    assert vmin == float(f"{vmin:.5g}") and vmax == float(f"{vmax:.5g}")  # 5 digits
    assert float(repr(vmin)) == vmin and float(repr(vmax)) == vmax  # JSON-safe
    assert compute_stretch([np.full(4, np.nan)], S) == (0.0, 1.0)
    lo, hi = compute_stretch([np.full(10, 2.5)], S)
    assert hi > lo


def test_gray_levels_and_fill_value():
    image = np.array([[np.nan, -5.0, 0.0, 0.5, 1.0, 7.0]])
    gray = to_gray8(image, 0.0, 1.0, S)
    assert gray.dtype == np.uint8
    assert gray[0, 0] == S.png_fill_value == 0
    assert gray[0, 1] == gray[0, 2] == 1  # at or below vmin
    assert gray[0, 4] == gray[0, 5] == 255  # at or above vmax
    assert 1 < gray[0, 3] < 255
    assert gray[0, 3] > 128  # asinh lifts the faint half
    with pytest.raises(InvalidParameter):
        to_gray8(image, 1.0, 1.0, S)


def test_one_stretch_maps_equal_values_to_equal_grey_in_every_frame():
    a = np.array([[0.2, 0.4, 5.0]])
    b = np.array([[0.4, 3.0, 0.2]])
    vmin, vmax = compute_stretch([a, b], S)
    ga, gb = to_gray8(a, vmin, vmax, S), to_gray8(b, vmin, vmax, S)
    assert ga[0, 0] == gb[0, 2] and ga[0, 1] == gb[0, 0]


def test_png_is_greyscale_and_north_row_first():
    gray = np.zeros((3, 3), dtype=np.uint8)
    gray[0, 1] = 200  # top row = north
    out = decode(encode_png(gray))
    assert out.shape == (3, 3) and out[0, 1] == 200
    assert encode_png(gray) == encode_png(gray.copy())


# ------------------------------------------------------------------ render_frame


def warm_cache(settings):
    """Download the fixture once (from the recorded SIA query and cutout)."""
    sia = FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    session = StreamSession(replies=[ok(FOV10)])
    png = render_frame(EPOCH, RA, DEC, 10, "D2", 0.05, 15.0, settings=settings,
                       session=session, sia_session=sia, query_cache=QueryCache())
    assert len(sia.calls) == 1 and len(session.calls) == 1
    return png


def test_render_frame_is_deterministic(tmp_path):
    settings = make_settings(tmp_path)
    first = warm_cache(settings)
    offline = StreamSession(replies=[])  # any network call fails the test
    no_sia = FakeSession(error=AssertionError("no SIA call expected"))
    again = render_frame(EPOCH, RA, DEC, 10, "D2", 0.05, 15.0, settings=settings,
                         session=offline, sia_session=no_sia)
    assert again == first
    assert offline.calls == [] and no_sia.calls == []
    gray = decode(first)
    assert gray.shape == (97, 97)
    assert gray[48, 48] == 255  # Barnard's Star at the centre, saturated in this stretch


def test_render_frame_needs_no_irsa_query_when_cached(tmp_path):
    settings = make_settings(tmp_path)
    warm_cache(settings)
    # A "restart": new query cache, IRSA unreachable. The cutout cache is enough.
    broken = FakeSession(error=AssertionError("no SIA call expected"))
    png = render_frame(EPOCH, RA, DEC, 10, "D2", settings=settings, sia_session=broken,
                       session=StreamSession(replies=[]), query_cache=QueryCache())
    assert decode(png).shape == (97, 97)


def test_render_frame_own_stretch_differs_from_shared(tmp_path):
    settings = make_settings(tmp_path)
    shared = warm_cache(settings)
    own = render_frame(EPOCH, RA, DEC, 10, "D2", settings=settings,
                       session=StreamSession(replies=[]))
    assert own != shared


@pytest.mark.parametrize("kwargs,error", [
    ({"vmin": 1.0}, InvalidParameter),
    ({"vmin": 2.0, "vmax": 1.0}, InvalidParameter),
    ({"vmin": float("nan"), "vmax": 1.0}, InvalidParameter),
    ({"vmin": True, "vmax": 2.0}, InvalidParameter),
    ({"fov": 0.5}, InvalidParameter),
    ({"band": "D9"}, InvalidParameter),
    ({"epoch_id": "nope"}, NotFound),
    ({"epoch_id": "sx_dr1_2025W34_2B_0090_4_D3"}, NotFound),  # other band
    ({"epoch_id": None}, NotFound),
])
def test_render_frame_rejects_bad_input_before_any_request(tmp_path, kwargs, error):
    args = {"epoch_id": EPOCH, "ra": RA, "dec": DEC, "fov": 10, "band": "D2"}
    args.update(kwargs)
    session = StreamSession(replies=[])
    sia = FakeSession(error=AssertionError("no SIA call expected"))
    with pytest.raises(error):
        render_frame(settings=make_settings(tmp_path), session=session, sia_session=sia,
                     **args)
    assert session.calls == [] and sia.calls == []


def test_render_frame_unknown_epoch_at_position(tmp_path):
    sia = FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    with pytest.raises(NotFound):
        render_frame("sx_dr1_2099W01_1A_0001_1_D2", RA, DEC, 10, "D2",
                     settings=make_settings(tmp_path), sia_session=sia,
                     session=StreamSession(replies=[]), query_cache=QueryCache())


def test_render_frame_upstream_failure(tmp_path):
    sia = FakeSession(response=FakeResponse(fixture_bytes("barnard_d2.xml")))
    session = StreamSession(replies=[StreamResponse(b"", status_code=404)])
    with pytest.raises(UpstreamError):
        render_frame(EPOCH, RA, DEC, 10, "D2", settings=make_settings(tmp_path),
                     sia_session=sia, session=session, query_cache=QueryCache())
