"""Cutout size rule, position rounding, wavelength at target and view coverage."""

from __future__ import annotations

import math
import warnings

import pytest
from astropy.io import fits
from astropy.wcs import WCS

from app.cutouts.geometry import (
    cutout_size_arcmin,
    measure,
    round_position,
    target_pixel,
    validate_fov,
    view_coverage,
    wavelength_at,
)
from app.errors import InvalidParameter
from app.models import BAND_RANGES_UM
from tests.cutout_helpers import FOV3, FOV10, fixture_path


@pytest.mark.parametrize(
    "fov, size", [(1, 2.5), (3, 5.3), (10, 15.2), (12.5, 18.7), (30, 43.5)]
)
def test_cutout_size_rule(fov, size):
    assert cutout_size_arcmin(fov) == size
    # A detector-aligned square of this side holds the view at any rotation.
    assert size / 2 >= fov / math.sqrt(2) + 0.5 - 1e-9


@pytest.mark.parametrize(
    "position, expected",
    [
        ((269.44641234, 4.76678), (269.4464, 4.7668)),
        ((359.99996, 0.0), (0.0, 0.0)),
        ((0.00004, -89.99996), (0.0, -90.0)),
        ((180.12346, 45.00004), (180.1235, 45.0)),
    ],
)
def test_round_position(position, expected):
    got = round_position(*position)
    assert got[0] == pytest.approx(expected[0], abs=1e-9)
    assert got[1] == pytest.approx(expected[1], abs=1e-9)
    assert 0.0 <= got[0] < 360.0


@pytest.mark.parametrize("fov", [0.5, 30.5, float("nan"), float("inf"), "10", True, None])
def test_bad_fov_rejected(fov):
    with pytest.raises(InvalidParameter):
        validate_fov(fov)


def test_fov_limits_accepted():
    assert validate_fov(1) == 1.0 and validate_fov(30.0) == 30.0


@pytest.mark.parametrize("entry", [FOV10, FOV3], ids=["fov10", "fov3"])
def test_wavelength_at_target_inside_array_range(entry):
    lo, hi = BAND_RANGES_UM["D2"]
    with fits.open(fixture_path(entry)) as hdul:
        wavelength = wavelength_at(hdul, entry["ra"], entry["dec"])
        pixel = target_pixel(hdul, entry["ra"], entry["dec"])
    assert wavelength is not None and lo <= wavelength <= hi
    assert wavelength == pytest.approx(entry["wavelength_um"], abs=1e-5)
    assert pixel is not None


def test_fixtures_differ_in_wavelength():
    """LVF: the wavelength depends on where the target falls on the detector."""
    assert FOV3["wavelength_um"] - FOV10["wavelength_um"] > 0.2


def test_full_coverage_at_target():
    for entry in (FOV10, FOV3):
        with fits.open(fixture_path(entry)) as hdul:
            assert view_coverage(hdul, entry["ra"], entry["dec"], entry["fov_arcmin"]) == 1.0


def _sky_of_pixel(hdul, x, y):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wcs = WCS(hdul["IMAGE"].header)
    ra, dec = wcs.all_pix2world([[x, y]], 0)[0]
    return float(ra), float(dec)


def test_partial_coverage_at_cutout_edge():
    with fits.open(fixture_path(FOV10)) as hdul:
        ny, nx = hdul["IMAGE"].data.shape
        ra, dec = _sky_of_pixel(hdul, nx - 0.5, ny / 2)  # on the right-hand edge
        coverage = view_coverage(hdul, ra, dec, 2.0)
        corner = _sky_of_pixel(hdul, nx - 0.5, ny - 0.5)
        corner_coverage = view_coverage(hdul, *corner, 2.0)
    assert 0.4 <= coverage <= 0.6
    assert 0.15 <= corner_coverage <= 0.35


def test_target_off_the_cutout():
    with fits.open(fixture_path(FOV3)) as hdul:
        ra, dec = FOV3["ra"], FOV3["dec"] + 1.0
        assert target_pixel(hdul, ra, dec) is None
        assert wavelength_at(hdul, ra, dec) is None
        assert view_coverage(hdul, ra, dec, 3.0) == 0.0
        assert measure(hdul, ra, dec, 3.0) == (None, 0.0)


def test_measure_matches_the_parts():
    with fits.open(fixture_path(FOV10)) as hdul:
        assert measure(hdul, FOV10["ra"], FOV10["dec"], 10.0) == (
            wavelength_at(hdul, FOV10["ra"], FOV10["dec"]),
            view_coverage(hdul, FOV10["ra"], FOV10["dec"], 10.0),
        )
