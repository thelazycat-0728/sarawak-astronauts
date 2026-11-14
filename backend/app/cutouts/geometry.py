"""Cutout size, cache-key rounding, and per-cutout measurements from the FITS WCS.

Rules (quoted by the T-004 report and later tasks):

- **Cutout size.** SPHEREx images are not north-aligned and IRSA cuts along the
  detector axes, so a detector-aligned square must contain a north-up
  ``fov`` x ``fov`` square at any rotation. Its side is ``fov * sqrt(2)`` plus a
  ``MARGIN_ARCMIN`` (1') margin for SIP distortion, pixel-scale changes across the
  detector and the reprojection kernel, rounded **up** to 0.1'. ``fov=10`` gives 15.2'.
- **Position rounding.** The cutout is requested at, and cached under, the view
  centre rounded to 4 decimal places of a degree (at most 0.18" off, under 3 % of a
  6.2" SPHEREx pixel). RA is wrapped into [0, 360). Measurements below use the exact
  requested position, not the rounded one.
- **Wavelength at target.** The target's pixel comes from the IMAGE extension's
  celestial WCS (with SIP). The wavelength is read at that pixel from the
  alternate WCS ``W`` (a ``WAVE-TAB`` lookup in the ``WCS-WAVE`` extension), as in
  IRSA's spherex_cutouts tutorial. ``None`` when the target is off the cutout.
- **Coverage.** The requested north-up view is sampled on a ``COVERAGE_SAMPLES``
  square grid of points (40 x 40, at the centres of the view's cells). Coverage is
  the fraction of points that land inside the cutout array. IRSA clips a cutout
  to the detector, and the cutout is larger than the view, so this is the fraction
  of the view that lies on the detector.
"""

from __future__ import annotations

import math
import numbers
from typing import Any

import astropy.units as u
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from app.errors import InvalidParameter

FOV_MIN_ARCMIN = 1.0
FOV_MAX_ARCMIN = 30.0
MARGIN_ARCMIN = 1.0
SIZE_STEP_ARCMIN = 0.1
POSITION_DECIMALS = 4
COVERAGE_SAMPLES = 40
# HDUs kept in a cached cutout (the primary HDU is always kept as well).
KEPT_HDUS = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")
_SIP_PREFIXES = ("A_", "B_", "AP_", "BP_")


def validate_fov(fov: object) -> float:
    """Field of view in arcminutes (API.md section 1: 1 to 30)."""
    if isinstance(fov, bool) or not isinstance(fov, numbers.Real):
        raise InvalidParameter("fov must be a number")
    value = float(fov)
    if not math.isfinite(value) or not FOV_MIN_ARCMIN <= value <= FOV_MAX_ARCMIN:
        raise InvalidParameter("fov must be between 1 and 30 arcmin")
    return value


def cutout_size_arcmin(fov_arcmin: float) -> float:
    """Side of the detector-aligned cutout that holds a rotated north-up view."""
    raw = fov_arcmin * math.sqrt(2.0) + MARGIN_ARCMIN
    steps = math.ceil(raw / SIZE_STEP_ARCMIN - 1e-9)
    return round(steps * SIZE_STEP_ARCMIN, 1)


def round_position(ra: float, dec: float) -> tuple[float, float]:
    """Cutout centre used for the request and the cache key."""
    ra_r = round(ra, POSITION_DECIMALS) % 360.0
    ra_r = round(ra_r, POSITION_DECIMALS) % 360.0
    dec_r = round(dec, POSITION_DECIMALS)
    return ra_r, max(-90.0, min(90.0, dec_r))


# ------------------------------------------------------------ WCS measurements
# These run in the batch thread pool, so they avoid warnings.catch_warnings (not
# thread-safe). SPHEREx headers parse without warnings (checked in T-004).


def _celestial_wcs(header: fits.Header) -> WCS:
    return WCS(header)


def _array_shape(header: fits.Header) -> tuple[int, int]:
    """(ny, nx) of an image HDU from its header, without reading the data."""
    return int(header["NAXIS2"]), int(header["NAXIS1"])


def _inside(x: Any, y: Any, shape: tuple[int, int]) -> Any:
    ny, nx = shape
    with np.errstate(invalid="ignore"):
        return (
            np.isfinite(x)
            & np.isfinite(y)
            & (x >= -0.5)
            & (x < nx - 0.5)
            & (y >= -0.5)
            & (y < ny - 0.5)
        )


def _sky_to_pixel(wcs: WCS, ra: Any, dec: Any) -> tuple[np.ndarray, np.ndarray]:
    """0-based pixel coordinates (numpy convention) of sky positions in degrees."""
    world = np.column_stack([np.atleast_1d(ra), np.atleast_1d(dec)])
    with np.errstate(all="ignore"):
        pix = wcs.all_world2pix(world, 0, quiet=True)
    return pix[:, 0], pix[:, 1]


def target_pixel(
    hdul: fits.HDUList, ra: float, dec: float, wcs: WCS | None = None
) -> tuple[float, float] | None:
    """Target pixel in the IMAGE array (0-based), or None when off the cutout."""
    header = hdul["IMAGE"].header
    x, y = _sky_to_pixel(wcs or _celestial_wcs(header), ra, dec)
    if not bool(_inside(x, y, _array_shape(header))[0]):
        return None
    return float(x[0]), float(y[0])


def wavelength_at(
    hdul: fits.HDUList, ra: float, dec: float, wcs: WCS | None = None
) -> float | None:
    """Central wavelength (um) at the target pixel from the WCS-WAVE lookup table.

    ``wcs`` is the IMAGE celestial WCS if the caller already has it."""
    pixel = target_pixel(hdul, ra, dec, wcs)
    if pixel is None:
        return None
    header = hdul["IMAGE"].header.copy()
    # The spectral WCS is a pixel lookup; drop SIP so astropy neither applies nor
    # warns about it (the tutorial sets ``sip = None`` after building the WCS).
    for key in [k for k in header if k.startswith(_SIP_PREFIXES)]:
        del header[key]
    spectral = WCS(header, fobj=hdul, key="W")
    wavelength = spectral.pixel_to_world(*pixel)[0]
    value = float(u.Quantity(wavelength).to_value(u.um))
    return round(value, 5) if math.isfinite(value) else None


def view_coverage(
    hdul: fits.HDUList,
    ra: float,
    dec: float,
    fov_arcmin: float,
    samples: int = COVERAGE_SAMPLES,
    wcs: WCS | None = None,
) -> float:
    """Fraction (0 to 1) of the north-up fov x fov view centred on (ra, dec) that
    falls inside the cutout array."""
    header = hdul["IMAGE"].header
    step = fov_arcmin / 60.0 / samples
    view = WCS(naxis=2)
    view.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    view.wcs.crval = [ra, dec]
    view.wcs.cdelt = [-step, step]  # north up, east left
    view.wcs.crpix = [(samples + 1) / 2.0, (samples + 1) / 2.0]
    ix, iy = np.meshgrid(np.arange(samples), np.arange(samples))
    sky_ra, sky_dec = view.all_pix2world(ix.ravel(), iy.ravel(), 0)
    x, y = _sky_to_pixel(wcs or _celestial_wcs(header), sky_ra, sky_dec)
    return round(float(np.mean(_inside(x, y, _array_shape(header)))), 4)


def measure(
    hdul: fits.HDUList, ra: float, dec: float, fov_arcmin: float
) -> tuple[float | None, float]:
    """(wavelength_um at the target, coverage of the view), sharing one WCS parse."""
    wcs = _celestial_wcs(hdul["IMAGE"].header)
    return (
        wavelength_at(hdul, ra, dec, wcs),
        view_coverage(hdul, ra, dec, fov_arcmin, wcs=wcs),
    )
