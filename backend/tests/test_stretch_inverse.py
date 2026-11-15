"""The grey-level convention in docs/API.md section 3, checked on real outputs.

``invert`` below is written from the API.md text, not imported, so these tests
fail if the code and the contract drift apart. Each rendered frame (the committed
mock PNGs and a pipeline frame from the recorded cutout) is turned back into
MJy/sr and compared with the values it was drawn from.
"""

from __future__ import annotations

import io
import json
import math
import sys

import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.models import EpochsResponse
from app.pipeline import build_sequence
from app.pipeline.clean import clean_cutout
from app.pipeline.render import to_gray8
from app.providers.mock import MockProvider
from app.stretch import gray_to_value, value_to_gray
from tests.conftest import FRONTEND_MOCK, REPO
from tests.cutout_helpers import FOV10, StreamSession, make_settings, ok
from tests.pipeline_helpers import (
    DEC,
    RA,
    detector_wcs,
    epoch,
    make_hdul,
    query_of,
    square,
)

sys.path.insert(0, str(REPO / "backend" / "scripts"))
import make_mock_data


def invert(gray: np.ndarray, vmin: float, vmax: float, a: float) -> np.ndarray:
    """API.md: g = 0 has no value; else v = vmin + (vmax - vmin) * a *
    sinh((g - 1) / 254 * asinh(1 / a))."""
    g = np.asarray(gray, dtype=np.float64)
    v = vmin + (vmax - vmin) * a * np.sinh((g - 1.0) / 254.0 * math.asinh(1.0 / a))
    return np.where(g == 0, np.nan, v)


def decode(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        assert im.mode == "L"
        return np.asarray(im)


def assert_recovered(values: np.ndarray, gray: np.ndarray, vmin: float, vmax: float,
                     a: float) -> None:
    """Every valid pixel inverts to its input within half a grey-level step (so
    within one step); values outside [vmin, vmax] land on grey 1 or 255."""
    valid = np.isfinite(values)
    assert np.array_equal(gray == 0, ~valid)  # grey 0 exactly where there is no data
    v, g = values[valid], gray[valid].astype(np.float64)
    assert g.min() >= 1
    assert np.all(g[v <= vmin] == 1) and np.all(g[v >= vmax] == 255)
    inside = (v > vmin) & (v < vmax)
    assert inside.sum() > 100
    tol = 1e-9 * (vmax - vmin)
    lo = invert(g[inside] - 0.5, vmin, vmax, a)
    hi = invert(g[inside] + 0.5, vmin, vmax, a)
    assert np.all(lo - tol <= v[inside]) and np.all(v[inside] <= hi + tol)
    step = invert(np.minimum(g[inside] + 1, 255), vmin, vmax, a) - invert(
        np.maximum(g[inside] - 1, 1), vmin, vmax, a)
    assert np.all(np.abs(invert(g[inside], vmin, vmax, a) - v[inside]) <= step)


# ------------------------------------------------------------------ app.stretch


def test_code_matches_the_documented_inverse():
    gray = np.arange(256)
    for a in (0.03, 0.1, 1.0):
        assert np.allclose(gray_to_value(gray, 0.2, 3.0, a), invert(gray, 0.2, 3.0, a),
                           equal_nan=True)
        assert np.isnan(gray_to_value(gray, 0.2, 3.0, a)[0])
        # Forward then inverse is the identity on the 255 valid levels.
        levels = gray_to_value(np.arange(1, 256), 0.2, 3.0, a)
        assert np.array_equal(value_to_gray(levels, 0.2, 3.0, a), np.arange(1, 256))
    with pytest.raises(ValueError):
        value_to_gray(np.zeros(2), 1.0, 1.0, 0.1)
    with pytest.raises(ValueError):
        gray_to_value(np.zeros(2), 0.0, 1.0, 0.0)


# ------------------------------------------------------------------ mock frames


def test_mock_frames_invert_to_their_mjysr_values():
    doc = EpochsResponse.model_validate_json(
        (FRONTEND_MOCK / "epochs.json").read_text(encoding="utf-8"))
    s = doc.stretch
    assert s.asinh_a == make_mock_data.STRETCH_ASINH_A == 0.03
    assert doc.image.nodata_gray == 0
    _rng, _mjds, frames, vmin, vmax = make_mock_data.simulate()
    assert (vmin, vmax) == (s.vmin, s.vmax)
    for e, values in zip(doc.epochs, frames, strict=True):
        gray = decode((FRONTEND_MOCK / "frames" / f"{e.id}.png").read_bytes())
        assert_recovered(values, gray, s.vmin, s.vmax, s.asinh_a)


def test_mock_flagged_fraction_is_the_share_of_grey_zero():
    doc = EpochsResponse.model_validate_json(
        (FRONTEND_MOCK / "epochs.json").read_text(encoding="utf-8"))
    shares = []
    for e in doc.epochs:
        gray = decode((FRONTEND_MOCK / "frames" / f"{e.id}.png").read_bytes())
        share = round(float(np.mean(gray == 0)), 4)
        assert e.quality.flagged_fraction == share, e.id
        shares.append(share)
    statuses = [e.quality.status for e in doc.epochs]
    assert statuses.count("warn") == 1 and statuses.count("bad") == 0
    assert sum(s > 0 for s in shares) == 3  # three frames carry no-data patches


def test_mock_provider_uses_the_same_inverse(provider: MockProvider):
    s = provider._doc.stretch
    e = provider._doc.epochs[0]
    png = (FRONTEND_MOCK / "frames" / f"{e.id}.png").read_bytes()
    expected = invert(decode(png), s.vmin, s.vmax, s.asinh_a)
    assert np.allclose(provider._to_mjysr(png), expected)


def test_mock_json_carries_the_new_fields():
    raw = json.loads((FRONTEND_MOCK / "epochs.json").read_text(encoding="utf-8"))
    assert raw["stretch"]["asinh_a"] == 0.03
    assert raw["image"]["nodata_gray"] == 0


# ------------------------------------------------------------------ pipeline frames


def test_pipeline_frame_inverts_to_its_mjysr_values(tmp_path):
    settings = make_settings(tmp_path)
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, lo, hi: f"/f/{i}.png",
                         mode="all", settings=settings,
                         session=StreamSession(replies=[ok(FOV10)]),
                         query=query_of([epoch("2025W34_2B_0090_4", 1, 60908.98,
                                               square(0.5))]))
    manifest = json.loads(seq.response.model_dump_json())
    s, image = manifest["stretch"], manifest["image"]
    assert s["asinh_a"] == settings.stretch_asinh_a == 0.1
    assert image["nodata_gray"] == 0
    ((epoch_id, png),) = seq.frames.items()
    values = seq.cleaned[epoch_id].image
    gray = decode(png)
    assert (gray == 0).sum() > 0  # this cutout has masked pixels
    assert_recovered(values, gray, s["vmin"], s["vmax"], s["asinh_a"])


def test_grey_zero_marks_off_detector_pixels():
    # Target 4' from the detector's west edge, with a view larger than the cutout.
    wcs = detector_wcs(rotation_deg=0.0, flip=False)
    image = np.random.default_rng(1).uniform(0.1, 5.0, size=(141, 149))
    edge_ra = RA - 4.0 / 60.0 / math.cos(math.radians(DEC))
    x_edge, _ = wcs.all_world2pix([[edge_ra, DEC]], 0)[0]
    image[:, round(x_edge):] = np.nan
    settings = Settings()
    frame = clean_cutout(make_hdul(image, wcs=wcs), RA, DEC, 20, settings)
    assert frame.coverage < 1.0
    gray = to_gray8(frame.image, 0.1, 5.0, settings)
    assert np.array_equal(gray == 0, ~frame.valid)
    assert (gray == 0).mean() == pytest.approx(frame.flagged_fraction, abs=1e-4)
    assert_recovered(frame.image, gray, 0.1, 5.0, settings.stretch_asinh_a)
