"""Cleaning one cutout: ZODI, masking, north-up reprojection and quality (offline)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from astropy.io import fits

from app.config import Settings
from app.errors import UpstreamError
from app.pipeline import flags as fl
from app.pipeline.clean import (
    clean_cutout,
    grid_side,
    quality_status,
    subtract_zodi,
    view_wcs,
)
from tests.cutout_helpers import FOV10, fixture_path
from tests.pipeline_helpers import (
    DEC,
    RA,
    SHAPE,
    add_source,
    bit,
    detector_wcs,
    make_hdul,
)

S = Settings()
CENTRE = 48  # 0-based centre pixel of the 97 px grid


def flat(value: float = 1.0) -> np.ndarray:
    return np.full(SHAPE, value, dtype=np.float64)


def peak(image: np.ndarray) -> tuple[int, int]:
    """(row, col) of the brightest valid pixel."""
    row, col = np.unravel_index(np.nanargmax(image), image.shape)
    return int(row), int(col)


# ------------------------------------------------------------------ grid


def test_grid_side_and_centre():
    assert grid_side(10, 6.2) == 97
    assert grid_side(1, 6.2) == 10
    assert grid_side(30, 6.2) == 291
    wcs, side = view_wcs(RA, DEC, 10, 6.2)
    assert side == 97
    x, y = wcs.all_world2pix([[RA, DEC]], 0)[0]
    assert (x, y) == pytest.approx((CENTRE, CENTRE))
    assert wcs.wcs.cdelt[0] < 0 < wcs.wcs.cdelt[1]  # east left, north up


# ------------------------------------------------------------------ ZODI


def test_zodi_is_subtracted():
    hdul = make_hdul(flat(5.0), zodi=2.0)
    assert np.allclose(subtract_zodi(hdul), 3.0)
    frame = clean_cutout(hdul, RA, DEC, 10, S)
    assert np.nanmax(np.abs(frame.image - 3.0)) < 1e-5


def test_zodi_units_are_converted():
    hdul = make_hdul(flat(5.0), zodi=2000.0, zodi_unit="kJy / sr")
    assert np.allclose(subtract_zodi(hdul), 3.0)


@pytest.mark.parametrize("unit", ["adu", "Jy", "not a unit"])
def test_zodi_unit_must_be_surface_brightness(unit):
    with pytest.raises(UpstreamError, match="surface brightness"):
        subtract_zodi(make_hdul(flat(), zodi_unit=unit))


def test_fixture_cutout_units_match():
    with fits.open(fixture_path(FOV10)) as hdul:
        assert hdul["IMAGE"].header["BUNIT"] == hdul["ZODI"].header["BUNIT"]
        diff = subtract_zodi(hdul)
        expected = hdul["IMAGE"].data.astype(float) - hdul["ZODI"].data.astype(float)
    assert np.allclose(diff, expected, equal_nan=True)


# ------------------------------------------------------------------ masking


def test_source_only_pixels_are_never_masked():
    hdul = make_hdul(flat(), flags=bit("SOURCE"))
    frame = clean_cutout(hdul, RA, DEC, 10, S)
    assert frame.flagged_fraction == 0.0 and frame.valid.all()
    assert frame.flags == [] and frame.status == "good"


def test_source_is_not_masked_even_when_configured():
    settings = Settings(pipeline_mask_flags=("SOURCE", "HOT"))
    frame = clean_cutout(make_hdul(flat(), flags=bit("SOURCE")), RA, DEC, 10, settings)
    assert frame.valid.all() and "SOURCE" not in frame.flags
    assert fl.mask_bits(["SOURCE"], fl.DEFAULT_BITS) == 0


def test_masked_bit_is_masked_and_named():
    flags = np.full(SHAPE, bit("SOURCE"), dtype=np.int64)
    flags[60:80, 60:80] |= bit("HOT")
    flags[10, 10] |= bit("FULLSAMPLE")  # not in the mask set
    frame = clean_cutout(make_hdul(flat(), flags=flags), RA, DEC, 10, S)
    assert 0 < frame.flagged_fraction < 0.25
    assert frame.flags == ["HOT"]
    assert not frame.valid.all()


def test_bits_come_from_the_header():
    # A file that puts HOT on bit 3 instead of 10.
    names = {"SOURCE": 21, "HOT": 3}
    hdul = make_hdul(flat(), flags=1 << 3, flag_names=names)
    frame = clean_cutout(hdul, RA, DEC, 10, S)
    assert frame.flagged_fraction == 1.0 and frame.flags == ["HOT"]
    assert fl.bits_from_header(fits.Header()) == fl.DEFAULT_BITS


def test_nan_pixels_are_masked_but_saturated_cores_are_filled():
    image = flat()
    image[70, 74] = 50.0
    image[70, 75] = np.nan  # saturated core next to a bright pixel
    image[20, 20] = np.nan  # plain bad pixel
    flags = np.zeros(SHAPE, dtype=np.int64)
    flags[70, 75] = bit("OVERFLOW")
    frame = clean_cutout(make_hdul(image, flags=flags), RA, DEC, 10, S)
    assert frame.saturated_px == 1
    assert np.nanmax(frame.image) > 40  # the core shows bright, not as a hole
    off = clean_cutout(make_hdul(image, flags=flags), RA, DEC, 10,
                       Settings(pipeline_fill_saturated=False))
    assert off.saturated_px == 0 and off.flagged_fraction > frame.flagged_fraction


# ------------------------------------------------------------------ orientation


@pytest.mark.parametrize("rotation,flip", [(0.0, False), (33.0, True), (147.0, False),
                                           (-80.0, True)])
def test_north_is_up_and_east_is_left(rotation, flip):
    wcs = detector_wcs(rotation_deg=rotation, flip=flip)
    offset_deg = 1.5 / 60.0  # 1.5' = 14.5 px at 6.2"/px
    expected = offset_deg * 3600 / 6.2

    north = flat(0.0)
    add_source(north, wcs, RA, DEC + offset_deg)
    frame = clean_cutout(make_hdul(north, wcs=wcs), RA, DEC, 10, S)
    row, col = peak(frame.image)
    assert row == pytest.approx(CENTRE - expected, abs=1)  # above the centre
    assert col == pytest.approx(CENTRE, abs=1)

    east = flat(0.0)
    add_source(east, wcs, RA + offset_deg / math.cos(math.radians(DEC)), DEC)
    frame = clean_cutout(make_hdul(east, wcs=wcs), RA, DEC, 10, S)
    row, col = peak(frame.image)
    assert col == pytest.approx(CENTRE - expected, abs=1)  # left of the centre
    assert row == pytest.approx(CENTRE, abs=1)


def test_target_lands_at_the_centre():
    image = flat(0.0)
    wcs = detector_wcs(rotation_deg=21.0)
    add_source(image, wcs, RA, DEC)
    frame = clean_cutout(make_hdul(image, wcs=wcs), RA, DEC, 10, S)
    assert peak(frame.image) == (CENTRE, CENTRE)


# ------------------------------------------------------------------ quality


@pytest.mark.parametrize("fraction,status", [
    (0.0, "good"), (0.0499, "good"), (0.05, "warn"), (0.2499, "warn"),
    (0.25, "bad"), (1.0, "bad"),
])
def test_quality_thresholds(fraction, status):
    assert quality_status(fraction, S) == status


def test_off_detector_pixels_count_as_flagged():
    # Target 4' from the detector's west edge: part of a 10' view is off it.
    wcs = detector_wcs(rotation_deg=0.0, flip=False)
    edge_ra = RA - 4.0 / 60.0 / math.cos(math.radians(DEC))
    x_edge, _ = wcs.all_world2pix([[edge_ra, DEC]], 0)[0]
    image = flat()
    image[:, round(x_edge):] = np.nan
    frame = clean_cutout(make_hdul(image, wcs=wcs), RA, DEC, 10, S)
    assert frame.status == "warn"
    assert frame.flagged_fraction == pytest.approx(0.1, abs=0.03)
    big = clean_cutout(make_hdul(flat(), wcs=wcs), RA, DEC, 30, S)
    assert big.coverage < 1.0 and big.status == "bad"
    assert big.flagged_fraction == pytest.approx(1 - big.coverage, abs=0.02)


def test_quality_of_the_recorded_cutout():
    with fits.open(fixture_path(FOV10)) as hdul:
        frame = clean_cutout(hdul, RA, DEC, 10, S)
    assert frame.image.shape == (97, 97)
    assert frame.coverage == 1.0
    assert frame.status in ("good", "warn")
    assert 0 < frame.flagged_fraction < 0.1
    assert set(frame.flags) <= set(S.pipeline_mask_flags)
    assert "SOURCE" not in frame.flags
    row, col = peak(frame.image)  # Barnard's Star, saturated core filled
    assert abs(row - CENTRE) <= 2 and abs(col - CENTRE) <= 2
    assert frame.saturated_px > 0
