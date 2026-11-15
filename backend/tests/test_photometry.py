"""Aperture photometry on native SPHEREx pixels (app.pipeline.photometry, T-009)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from astropy.io import fits

from app.config import Settings
from app.errors import UpstreamError
from app.pipeline.clean import clean_cutout
from app.pipeline.photometry import (
    ANNULUS_GAP_ARCSEC,
    ANNULUS_WIDTH_ARCSEC,
    METHOD,
    MIN_SKY_PX,
    UJY_PER_MJY,
    Stamp,
    annulus_radii,
    extract_stamp,
    measure,
    pixel_solid_angle,
)
from tests.cutout_helpers import FOV10, fixture_path
from tests.pipeline_helpers import (
    DEC,
    RA,
    SCALE_ARCSEC,
    SHAPE,
    bit,
    detector_wcs,
    make_hdul,
)

ARCSEC_PER_RAD = 180.0 / math.pi * 3600.0
PIXEL_SR = (SCALE_ARCSEC / ARCSEC_PER_RAD) ** 2
# A field star in the recorded Barnard's Star cutout, 22 px from Barnard's core.
FIELD_STAR = (269.42455, 4.79809)


def gaussian(amplitude: float, sigma_px: float, x: float, y: float,
             shape: tuple[int, int] = SHAPE) -> np.ndarray:
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma_px**2))


def with_variance(hdul: fits.HDUList, variance: np.ndarray | float,
                  unit: str = "MJy2 / sr2") -> fits.HDUList:
    shape = hdul["IMAGE"].data.shape
    data = np.broadcast_to(np.asarray(variance, dtype=np.float32), shape).copy()
    hdu = fits.ImageHDU(data, header=hdul["IMAGE"].header.copy(), name="VARIANCE")
    hdu.header["BUNIT"] = unit
    return fits.HDUList([*hdul, hdu])


def target_xy(ra: float = RA, dec: float = DEC) -> tuple[float, float]:
    x, y = detector_wcs().all_world2pix([[ra, dec]], 0)[0]
    return float(x), float(y)


def star_flux_ujy(amplitude: float, sigma_px: float) -> float:
    """Total flux of a sampled 2-D Gaussian of peak ``amplitude`` MJy/sr."""
    return 2 * math.pi * amplitude * sigma_px**2 * PIXEL_SR * UJY_PER_MJY


def synthetic(amplitude=50.0, sigma_px=1.2, sky=0.0, flags=None, variance=None,
              offset_px=(0.3, -0.2)):
    x, y = target_xy()
    image = gaussian(amplitude, sigma_px, x + offset_px[0], y + offset_px[1]) + sky
    hdul = make_hdul(image, zodi=0.0, flags=0 if flags is None else flags)
    if variance is not None:
        hdul = with_variance(hdul, variance)
    # The target is the star's position: offset_px moves the star off the pixel grid.
    ra, dec = detector_wcs().all_pix2world([[x + offset_px[0], y + offset_px[1]]], 0)[0]
    return hdul, float(ra), float(dec)


# ------------------------------------------------------------ flux and units


def test_pixel_solid_angle_from_the_wcs():
    x, y = target_xy()
    assert pixel_solid_angle(detector_wcs(), x, y) == pytest.approx(PIXEL_SR, rel=1e-4)


def test_gaussian_flux_is_recovered_within_2_percent_in_a_large_aperture():
    hdul, ra, dec = synthetic(variance=1e-4)
    stamp = extract_stamp(hdul, ra, dec, Settings())
    assert stamp is not None
    assert stamp.pixel_scale_arcsec == pytest.approx(SCALE_ARCSEC, rel=1e-4)
    m = measure(stamp, 60.0)
    want = star_flux_ujy(50.0, 1.2)
    assert m.quality == "good"
    assert m.flux == pytest.approx(want, rel=0.02)
    assert m.flux_err is not None and m.flux_err > 0
    # The default 12" aperture (about 2 px) loses the wings: no aperture correction.
    small = measure(stamp, 12.0)
    assert 0.5 * want < small.flux < m.flux


def test_constant_sky_is_removed():
    plain, ra, dec = synthetic()
    sky, _, _ = synthetic(sky=7.5)
    settings = Settings()
    for aperture in (12.0, 30.0, 60.0):
        a = measure(extract_stamp(plain, ra, dec, settings), aperture)
        b = measure(extract_stamp(sky, ra, dec, settings), aperture)
        assert b.flux == pytest.approx(a.flux, abs=0.2)


def test_zodi_is_subtracted_before_measuring():
    x, y = target_xy()
    image = gaussian(50.0, 1.2, x, y)
    ramp = np.linspace(0.0, 30.0, SHAPE[1])[None, :] * np.ones(SHAPE)
    with_zodi = make_hdul(image + ramp, zodi=ramp)
    without = make_hdul(image)
    settings = Settings()
    a = measure(extract_stamp(with_zodi, RA, DEC, settings), 24.0)
    b = measure(extract_stamp(without, RA, DEC, settings), 24.0)
    assert a.flux == pytest.approx(b.flux, rel=1e-4)


def test_unit_conversion_mjy_per_sr_to_ujy():
    # 1 MJy/sr on a disc of 13 pixel centres (radius 2 px), zero sky around it.
    yy, xx = np.mgrid[0:37, 0:37]
    disc = np.hypot(xx - 18, yy - 18) <= 2.0
    image = np.where(disc, 1.0, 0.0).astype(np.float32)
    stamp = Stamp(image=image, variance=None, x=18.0, y=18.0, pixel_sr=PIXEL_SR)
    m = measure(stamp, 6.0 * stamp.pixel_scale_arcsec)  # covers the disc fully
    # MJy/sr x sr = MJy, and 1 MJy = 1e12 uJy.
    assert m.flux == pytest.approx(13 * PIXEL_SR * 1e12, abs=0.1)
    assert UJY_PER_MJY == 1e12
    assert 13 * PIXEL_SR * 1e12 == pytest.approx(13 * 6.15**2 * 2.35045e1, rel=1e-4)


def test_flux_err_is_the_variance_plane_plus_the_sky_term():
    # A flat field: zero sky scatter, so the error is the VARIANCE term alone.
    v = 0.01
    stamp = Stamp(image=np.zeros((37, 37), np.float32),
                  variance=np.full((37, 37), v, np.float32), x=18.2, y=17.9,
                  pixel_sr=PIXEL_SR)
    m = measure(stamp, 12.0)
    from photutils.aperture import CircularAperture

    w = CircularAperture((18.2, 17.9), 12.0 / stamp.pixel_scale_arcsec).to_mask(
        method="exact").to_image((37, 37))
    want = math.sqrt(float(np.sum(w**2)) * v) * PIXEL_SR * 1e12
    assert m.flux == 0.0 and m.flux_err == pytest.approx(want, abs=0.1)
    # Sky scatter adds the error of the median.
    rng = np.random.default_rng(1)
    noisy = Stamp(image=rng.normal(0, 0.1, (37, 37)).astype(np.float32),
                  variance=stamp.variance, x=18.2, y=17.9, pixel_sr=PIXEL_SR)
    assert measure(noisy, 12.0).flux_err > m.flux_err


def test_without_a_variance_plane_the_error_is_the_annulus_scatter():
    rng = np.random.default_rng(2)
    image = rng.normal(0, 0.1, (37, 37)).astype(np.float32)
    m = measure(Stamp(image, None, 18.0, 18.0, PIXEL_SR), 12.0)
    assert m.flux_err is not None and m.flux_err > 0
    assert m.quality == "good"


def test_variance_units_are_converted_and_checked():
    hdul, ra, dec = synthetic()
    mjy = extract_stamp(with_variance(hdul, 1e-4), ra, dec, Settings())
    jy = extract_stamp(with_variance(hdul, 1e8, unit="Jy2 / sr2"), ra, dec, Settings())
    assert np.nanmax(np.abs(jy.variance - 1e-4)) < 1e-9
    assert np.nanmax(np.abs(mjy.variance - 1e-4)) < 1e-9
    with pytest.raises(UpstreamError, match="VARIANCE unit"):
        extract_stamp(with_variance(hdul, 1.0, unit="adu"), ra, dec, Settings())


# ------------------------------------------------------------ quality rules


def test_a_masked_pixel_in_the_aperture_is_null_and_bad():
    x, y = target_xy()
    flags = np.zeros(SHAPE, np.int32)
    flags[round(y), round(x) + 1] = bit("HOT")
    hdul, ra, dec = synthetic(flags=flags, offset_px=(0.0, 0.0))
    m = measure(extract_stamp(hdul, ra, dec, Settings()), 12.0)
    assert (m.flux, m.flux_err, m.quality) == (None, None, "bad")


def test_a_masked_pixel_outside_the_aperture_does_not_matter():
    x, y = target_xy()
    flags = np.zeros(SHAPE, np.int32)
    flags[round(y), round(x) + 3] = bit("HOT")  # 18" away, in the gap
    hdul, ra, dec = synthetic(flags=flags, offset_px=(0.0, 0.0))
    m = measure(extract_stamp(hdul, ra, dec, Settings()), 12.0)
    assert m.quality == "good" and m.flux is not None


def test_a_saturated_core_filled_for_display_is_never_measured():
    x, y = target_xy()
    ix, iy = round(x), round(y)
    image = gaussian(5000.0, 1.2, x, y)
    image[iy - 1:iy + 2, ix - 1:ix + 2] = np.nan  # the saturated core
    flags = np.zeros(SHAPE, np.int32)
    flags[iy - 1:iy + 2, ix - 1:ix + 2] = bit("OVERFLOW")
    hdul = make_hdul(image, flags=flags)
    settings = Settings()
    frame = clean_cutout(hdul, RA, DEC, 10.0, settings)
    assert frame.filled is not None and frame.filled.any()  # displayed as filled
    for aperture in (3.0, 12.0, 60.0):
        m = measure(extract_stamp(hdul, RA, DEC, settings), aperture)
        assert (m.flux, m.flux_err, m.quality) == (None, None, "bad")


def test_an_aperture_off_the_frame_is_null_and_bad():
    # The target 1 px inside the cutout's east edge: the aperture runs off it.
    wcs = detector_wcs()
    ra, dec = (float(v) for v in wcs.all_pix2world([[1.0, SHAPE[0] / 2]], 0)[0])
    hdul = make_hdul(gaussian(50.0, 1.2, 1.0, SHAPE[0] / 2))
    stamp = extract_stamp(hdul, ra, dec, Settings())
    assert stamp is not None
    assert measure(stamp, 12.0).quality == "bad"
    # Off the cutout altogether: no stamp, and the point is bad.
    far = extract_stamp(hdul, RA + 1.0, DEC, Settings())
    assert far is None
    assert measure(far, 12.0) == measure(None, 12.0)
    assert measure(None, 12.0).flux is None and measure(None, 12.0).quality == "bad"


def test_a_thin_annulus_is_warn_and_an_empty_one_bad():
    yy, xx = np.mgrid[0:37, 0:37]
    r = np.hypot(xx - 18, yy - 18)
    stamp = Stamp(np.zeros((37, 37), np.float32), None, 18.0, 18.0, PIXEL_SR)
    r_in, r_out = (v / stamp.pixel_scale_arcsec for v in annulus_radii(12.0))
    ring = (r >= r_in) & (r <= r_out)
    thin = stamp.image.copy()
    thin[ring & (xx < 24)] = np.nan  # most of the annulus masked
    m = measure(Stamp(thin, None, 18.0, 18.0, PIXEL_SR), 12.0)
    assert m.sky_px < 0.5 * m.annulus_px and m.sky_px >= MIN_SKY_PX
    assert m.quality == "warn" and m.flux is not None
    empty = stamp.image.copy()
    empty[ring] = np.nan
    m = measure(Stamp(empty, None, 18.0, 18.0, PIXEL_SR), 12.0)
    assert (m.flux, m.quality) == (None, "bad")


def test_frame_quality_warn_or_bad_makes_the_point_warn():
    stamp = Stamp(np.zeros((37, 37), np.float32), None, 18.0, 18.0, PIXEL_SR)
    assert measure(stamp, 12.0, "good").quality == "good"
    assert measure(stamp, 12.0, "warn").quality == "warn"
    assert measure(stamp, 12.0, "bad").quality == "warn"


def test_aperture_range_and_annulus_radii():
    stamp = Stamp(np.zeros((37, 37), np.float32), None, 18.0, 18.0, PIXEL_SR)
    for bad in (2.9, 60.1, float("nan")):
        with pytest.raises(ValueError):
            measure(stamp, bad)
    assert measure(stamp, 3.0).quality == "good"
    assert annulus_radii(12.0) == (12.0 + ANNULUS_GAP_ARCSEC,
                                   12.0 + ANNULUS_GAP_ARCSEC + ANNULUS_WIDTH_ARCSEC)
    assert annulus_radii(12.0) == (24.0, 48.0)


def test_the_largest_aperture_and_annulus_fit_in_the_stamp():
    hdul, ra, dec = synthetic()
    stamp = extract_stamp(hdul, ra, dec, Settings())
    r_out_px = annulus_radii(60.0)[1] / stamp.pixel_scale_arcsec
    side = stamp.image.shape[0]
    assert stamp.x - r_out_px >= 0 and stamp.x + r_out_px <= side - 1
    assert measure(stamp, 60.0).quality == "good"


def test_method_text_names_the_rules_and_the_colour_caveat():
    for words in ("native", "ZODI", "annulus", "VARIANCE", "No aperture correction",
                  "null", "colour"):
        assert words in METHOD


# ------------------------------------------------------------ recorded cutout


def test_recorded_cutout_barnards_core_is_bad_and_a_field_star_is_measured():
    settings = Settings()
    with fits.open(fixture_path(FOV10)) as hdul:
        barnard = extract_stamp(hdul, RA, DEC, settings)
        star = extract_stamp(hdul, *FIELD_STAR, settings)
    assert barnard is not None and star is not None
    assert measure(barnard, 12.0).quality == "bad"  # saturated in every exposure
    m = measure(star, 12.0)
    assert m.quality == "good" and m.flux > 10 * m.flux_err > 0
    assert star.variance is not None
    assert star.image.dtype == np.float32 and star.image.shape[0] < 41
