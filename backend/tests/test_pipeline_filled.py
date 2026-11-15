"""Filled saturated cores (T-005 review, MED 1): shown, but marked, left out of the
stretch and excluded from anything that measures."""

from __future__ import annotations

import numpy as np
from astropy.io import fits

from app.config import Settings
from app.pipeline import build_sequence
from app.pipeline.clean import clean_cutout
from app.pipeline.render import compute_stretch
from tests.cutout_helpers import FOV10, StreamSession, fixture_path, make_settings, ok
from tests.pipeline_helpers import (
    DEC,
    RA,
    SHAPE,
    bit,
    detector_wcs,
    epoch,
    make_hdul,
    query_of,
    square,
)

S = Settings()


def saturated_star() -> fits.HDUList:
    """A flat field with a bright star whose 3x3 core is NaN with OVERFLOW set."""
    wcs = detector_wcs(rotation_deg=0.0, flip=False)
    image = np.full(SHAPE, 1.0)
    x, y = (round(v) for v in wcs.all_world2pix([[RA, DEC]], 0)[0])
    image[y - 2:y + 3, x - 2:x + 3] = 500.0
    image[y - 1:y + 2, x - 1:x + 2] = np.nan
    flags = np.zeros(SHAPE, dtype=np.int64)
    flags[y - 1:y + 2, x - 1:x + 2] = bit("OVERFLOW")
    return make_hdul(image, flags=flags, wcs=wcs)


def test_filled_pixels_are_marked_shown_and_not_flagged():
    frame = clean_cutout(saturated_star(), RA, DEC, 10, S)
    assert frame.saturated_px == 9
    filled = frame.filled_mask
    assert frame.filled is not None and 9 <= filled.sum() <= 40
    assert filled.shape == frame.image.shape
    assert filled[48, 48]  # the target's core, at the view centre
    assert np.all(np.isfinite(frame.image[filled]))  # displayed
    assert np.all(frame.image[filled] > 1.0)
    # Displayable, so not flagged: the same flagged fraction as a frame whose core
    # really holds those values.
    assert frame.flagged_fraction == 0.0 and frame.status == "good"
    assert frame.core_valid == 1.0
    # Excluded from anything that measures.
    assert np.all(np.isnan(frame.measurable[filled]))
    assert np.array_equal(frame.measurable[~filled], frame.image[~filled])


def test_without_the_fill_nothing_is_marked():
    frame = clean_cutout(saturated_star(), RA, DEC, 10,
                         Settings(pipeline_fill_saturated=False))
    assert frame.filled is None and not frame.filled_mask.any()
    assert frame.measurable is frame.image
    assert frame.flagged_fraction > 0  # the unfilled core is a hole


def test_filled_pixels_are_left_out_of_the_stretch():
    # A 2' view (20 x 20 px), so the filled core is more than the top 0.5 %.
    frame = clean_cutout(saturated_star(), RA, DEC, 2, S)
    assert frame.filled_mask.mean() > 0.01
    with_fill = compute_stretch([frame.image], S)
    measured = compute_stretch([frame.measurable], S)
    assert measured != with_fill
    assert measured[1] < with_fill[1]  # the fake bright core no longer sets vmax


def test_recorded_cutout_marks_barnards_core_and_the_sequence_stretch_skips_it(tmp_path):
    with fits.open(fixture_path(FOV10)) as hdul:
        frame = clean_cutout(hdul, RA, DEC, 10, S)
    assert frame.saturated_px > 0 and frame.filled_mask.sum() >= frame.saturated_px
    rows, cols = np.nonzero(frame.filled_mask)
    assert abs(rows.mean() - 48) <= 3 and abs(cols.mean() - 48) <= 3

    seq = build_sequence(RA, DEC, "D2", 10, frame_url=lambda i, lo, hi: f"/f/{i}.png",
                         mode="all", settings=make_settings(tmp_path),
                         session=StreamSession(replies=[ok(FOV10)]),
                         query=query_of([epoch("2025W34_2B_0090_4", 1, 60908.98,
                                               square(0.5))]))
    (cleaned,) = seq.cleaned.values()
    s = seq.response.stretch
    assert (s.vmin, s.vmax) == compute_stretch([cleaned.measurable], S)
    assert (s.vmin, s.vmax) != compute_stretch([cleaned.image], S)
