"""Per-visit stacks (app.pipeline.stack): valid-pixel mean, no-data handling,
filled saturated pixels kept out of the measurable stack, fixed-stretch render."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.config import Settings
from app.errors import InvalidParameter
from app.pipeline import stack_frames, stack_png
from app.pipeline.clean import CleanFrame
from app.pipeline.render import frame_png
from app.stretch import NODATA_GRAY, value_to_gray

NAN = np.nan


def frame(image, flags=(), filled=None) -> CleanFrame:
    image = np.asarray(image, dtype=np.float64)
    return CleanFrame(image=image, flagged_fraction=float(np.mean(~np.isfinite(image))),
                      status="good", flags=list(flags), coverage=1.0, saturated_px=0,
                      filled=None if filled is None else np.asarray(filled, dtype=bool))


def test_mean_uses_only_valid_pixels():
    a = frame([[1.0, 2.0], [NAN, 4.0]])
    b = frame([[3.0, NAN], [NAN, 8.0]])
    c = frame([[5.0, 6.0], [NAN, NAN]])
    s = stack_frames([a, b, c], Settings())
    np.testing.assert_allclose(s.image[0], [3.0, 4.0])
    assert s.image[1, 1] == 6.0
    assert s.n_valid.tolist() == [[3, 2], [0, 2]]
    assert s.n_frames == 3


def test_pixel_with_no_valid_input_is_no_data():
    a = frame([[NAN, 1.0], [NAN, 1.0]])
    b = frame([[NAN, 2.0], [NAN, NAN]])
    s = stack_frames([a, b], Settings())
    assert np.isnan(s.image[:, 0]).all()
    assert s.flagged_fraction == 0.5 and s.status == "bad"
    gray = value_to_gray(s.image, 0.0, 2.0, 0.1)
    assert (gray[:, 0] == NODATA_GRAY).all() and (gray[:, 1] > 0).all()


def test_quality_flags_are_the_union_in_first_seen_order():
    s = stack_frames([frame(np.ones((4, 4)), ["HOT", "PERSIST"]),
                      frame(np.ones((4, 4)), ["COLD", "HOT"])], Settings())
    assert s.flags == ["HOT", "PERSIST", "COLD"]
    assert s.flagged_fraction == 0.0 and s.status == "good"


def test_filled_pixels_show_in_the_display_stack_but_not_in_measurable():
    filled = [[True, False], [False, False]]
    a = frame([[100.0, 1.0], [1.0, 1.0]], filled=filled)
    b = frame([[50.0, 3.0], [NAN, 1.0]])
    s = stack_frames([a, b], Settings())
    assert s.image[0, 0] == 75.0  # displayed
    assert s.filled is not None and s.filled.tolist() == [[True, False], [False, False]]
    m = s.measurable
    assert np.isnan(m[0, 0]) and m[0, 1] == 2.0 and m[1, 0] == 1.0
    assert not np.isnan(s.image).any()  # the display image keeps the filled value


def test_stack_without_filled_pixels_has_no_mask():
    s = stack_frames([frame(np.ones((3, 3)), filled=np.zeros((3, 3)))], Settings())
    assert s.filled is None and s.measurable is s.image


def test_single_frame_stack_renders_like_the_frame_with_a_fixed_stretch():
    settings = Settings()
    rng = np.random.default_rng(1)
    img = rng.normal(0.0, 1.0, (9, 9))
    img[0, 0] = NAN
    f = frame(img)
    s = stack_frames([f], settings)
    assert stack_png(s, -1.0, 2.0, settings) == frame_png(f, -1.0, 2.0, settings)


def test_stack_png_uses_the_given_stretch_not_its_own():
    settings = Settings()
    s = stack_frames([frame([[0.0, 10.0], [20.0, NAN]])], settings)
    gray = np.asarray(Image.open(io.BytesIO(stack_png(s, 0.0, 10.0, settings))))
    assert gray[0, 0] == 1 and gray[0, 1] == 255 and gray[1, 0] == 255
    assert gray[1, 1] == NODATA_GRAY
    other = np.asarray(Image.open(io.BytesIO(stack_png(s, 0.0, 40.0, settings))))
    assert other[0, 1] < 255  # a different stretch gives different grey levels
    with pytest.raises(InvalidParameter):
        stack_png(s, 1.0, 1.0, settings)


def test_stack_needs_frames_of_one_size():
    with pytest.raises(InvalidParameter):
        stack_frames([], Settings())
    with pytest.raises(InvalidParameter):
        stack_frames([frame(np.ones((2, 2))), frame(np.ones((3, 3)))], Settings())


def test_noise_drops_with_the_number_of_frames():
    rng = np.random.default_rng(7)
    frames = [frame(rng.normal(0.0, 1.0, (60, 60))) for _ in range(6)]
    s = stack_frames(frames, Settings())
    assert 0.33 < float(np.std(s.image)) < 0.50  # about 1 / sqrt(6) = 0.41
