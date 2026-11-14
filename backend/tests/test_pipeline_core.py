"""Target-core filter and parameter checks (T-006 fix-1 additions; offline)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from app.cutouts import fetch_cutouts
from app.errors import InvalidParameter
from app.pipeline import build_sequence
from app.pipeline.clean import core_valid_fraction
from app.pipeline.render import clean_path
from app.pipeline.select import select_per_visit
from tests.cutout_helpers import FOV10, StreamSession, make_settings, ok
from tests.pipeline_helpers import DEC, RA, epoch, query_of, square

SAFE = square(0.5)


def visits():
    v1 = [epoch(f"2025W34_2B_0090_{i}", 1, 60908.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    v2 = [epoch(f"2026W12_1A_0311_{i}", 2, 61116.0 + i * 0.1, SAFE) for i in (1, 2, 3)]
    return v1 + v2


def mask_core(frame, keep: int = 0):
    """The frame with its central 5x5 box set to NaN, except ``keep`` pixels."""
    img = frame.image.copy()
    ny, nx = img.shape
    cy, cx = (ny - 1) // 2, (nx - 1) // 2
    box = img[cy - 2:cy + 3, cx - 2:cx + 3]
    flat = box.reshape(-1)
    flat[keep:] = np.nan
    img[cy - 2:cy + 3, cx - 2:cx + 3] = flat.reshape(box.shape)
    return replace(frame, image=img)


def run(session, settings, masked_ids=(), **kwargs):
    def fetch(items):
        return fetch_cutouts(items, RA, DEC, 10, settings=settings, session=session).results

    def clean(cutout):
        frame = clean_path(cutout.path, RA, DEC, 10, settings)
        return mask_core(frame) if any(m in cutout.epoch_id for m in masked_ids) else frame

    return select_per_visit(visits(), RA, DEC, 10, fetch=fetch, clean=clean,
                            settings=settings, **kwargs)


def test_core_valid_fraction():
    img = np.ones((9, 9))
    assert core_valid_fraction(img) == 1.0
    img[4, 4] = np.nan  # 1 of the 13 pixels within 2 px of the centre
    assert core_valid_fraction(img) == pytest.approx(12 / 13, abs=1e-4)
    img[2:7, 2:7] = np.nan
    assert core_valid_fraction(img) == 0.0
    img[0, 0] = np.nan  # outside the core: no effect
    even = np.ones((10, 10))  # centre on a pixel corner: 12 pixels within 2 px
    even[4:6, 4:6] = np.nan
    assert core_valid_fraction(even) == pytest.approx(8 / 12, abs=1e-4)


def test_masked_core_is_rejected_and_replaced(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    # 0090_2 is visit 1's best-ranked candidate; its core is masked.
    result = run(session, settings, masked_ids=("0090_2",), min_core_valid=0.5)
    outcomes = {a.epoch_id: a.outcome for a in result.attempts}
    assert outcomes["sx_dr1_2025W34_2B_0090_2_D2"] == "masked_core"
    assert "core valid 0.00" in next(a.detail for a in result.attempts
                                     if a.outcome == "masked_core")
    v1 = [c for c in result.chosen if c.epoch.visit == 1]
    assert len(v1) == 1 and v1[0].accepted and "0090_2" not in v1[0].epoch.id


def test_core_filter_is_off_by_default(tmp_path):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[ok(FOV10)])
    result = run(session, settings, masked_ids=("0090_2",))
    assert {a.outcome for a in result.attempts} == {"accepted"}
    assert "core valid" not in result.attempts[0].detail


@pytest.mark.parametrize("kwargs", [
    {"max_per_visit": 0}, {"max_per_visit": -1}, {"max_per_visit": True},
    {"max_per_visit": 1.5}, {"max_candidates": 0}, {"max_candidates": -2},
    {"min_core_valid": 1.5}, {"min_core_valid": -0.1}, {"min_core_valid": "half"},
])
def test_bad_selection_parameters_raise(tmp_path, kwargs):
    settings = make_settings(tmp_path)
    session = StreamSession(replies=[])  # nothing may be fetched
    with pytest.raises(InvalidParameter):
        run(session, settings, **kwargs)
    assert session.calls == []


@pytest.mark.parametrize("kwargs", [
    {"max_per_visit": 0}, {"max_candidates": 0}, {"stretch": "ab"}, {"stretch": (1.0,)},
    {"stretch": (1.0, 2.0, 3.0)}, {"stretch": ("a", "b")}, {"stretch": (True, 2.0)},
    {"stretch": 5}, {"stretch": (2.0, 1.0)}, {"stretch": (0.0, float("inf"))},
])
def test_bad_build_parameters_raise_before_any_call(tmp_path, kwargs):
    session = StreamSession(replies=[])
    with pytest.raises(InvalidParameter):
        build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, a, b: "",
                       settings=make_settings(tmp_path), session=session,
                       query=query_of(visits()), **kwargs)
    assert session.calls == []


def test_build_sequence_passes_min_core_valid(tmp_path):
    settings = make_settings(tmp_path)
    seq = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, a, b: "", settings=settings,
                         session=StreamSession(replies=[ok(FOV10)]), query=query_of(visits()),
                         min_core_valid=1.0)  # the fixture's (filled) core is all valid
    assert {a.outcome for a in seq.attempts} == {"accepted"}
    assert all("core valid 1.00" in a.detail for a in seq.attempts)
    assert seq.response.count == 2
