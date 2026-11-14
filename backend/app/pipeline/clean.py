"""Clean one SPHEREx cutout onto the shared north-up grid of a view.

Steps, in order (TECH_STACK section 3, "Data cleaning applied to every image"):

1. **ZODI subtraction.** ``IMAGE - ZODI``, both converted to MJy/sr from their
   ``BUNIT`` (IRSA: the ZODI model "has not been subtracted from the IMAGE
   extension"). A unit that is not a surface brightness raises UpstreamError.
2. **Mask.** Pixels with any bit of ``Settings.pipeline_mask_flags`` set, and pixels
   that are not finite, become NaN. ``SOURCE`` never masks (see ``flags``).
   Saturated cores (not finite, OVERFLOW set, no mask bit) are filled with the
   brightest valid pixel within 2 px when ``pipeline_fill_saturated`` is on.
3. **Reproject** with ``reproject.reproject_interp`` (bilinear, using the IMAGE WCS
   with its SIP distortion) onto a TAN grid centred on the target: north up, east
   left, ``pixel_scale_arcsec`` per pixel, ``side = ceil(fov * 60 / scale)`` pixels.
   A NaN input pixel makes every output pixel that interpolates from it NaN.
4. **Quality** (API.md section 3). ``flagged_fraction`` is the fraction of view
   pixels with no valid value after step 3: masked, off the detector, or NaN.
   ``status`` is ``good`` below ``quality_warn_fraction`` (5 %), ``warn`` below
   ``quality_bad_fraction`` (25 %), else ``bad``. ``flags`` lists the masked flag
   names (from the configured set) present in the view, found by a
   nearest-neighbour reprojection of FLAGS; never ``SOURCE``.

The returned image is in display order: row 0 is the north edge, column 0 the east
edge, so it can be written to a PNG as is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp
from scipy.ndimage import maximum_filter

from app.config import Settings
from app.errors import UpstreamError
from app.pipeline import flags as fl

MJY_SR = u.MJy / u.sr
SATURATED_FILL_PX = 2  # search radius for filling a saturated core


@dataclass(frozen=True)
class CleanFrame:
    image: np.ndarray  # float64 MJy/sr, display order (row 0 = north), NaN = no data
    flagged_fraction: float
    status: str  # good, warn or bad
    flags: list[str]
    coverage: float  # fraction of the view on the detector (nearest-neighbour)
    saturated_px: int  # input pixels filled as saturated cores

    @property
    def valid(self) -> np.ndarray:
        return np.isfinite(self.image)

    @property
    def core_valid(self) -> float:
        """Valid fraction of the target's core (r <= 2 px of the view centre)."""
        return core_valid_fraction(self.image)


CORE_RADIUS_PX = 2.0  # the target's core, for core_valid_fraction


def core_valid_fraction(image: np.ndarray, radius_px: float = CORE_RADIUS_PX) -> float:
    """Fraction of pixels within ``radius_px`` of the image centre (the target)
    that hold a value. Filled saturated cores count as valid; masked, off-detector
    and NaN pixels do not. 0 when no pixel is that close (an empty image)."""
    ny, nx = image.shape
    yy, xx = np.indices(image.shape)
    core = np.hypot(xx - (nx - 1) / 2.0, yy - (ny - 1) / 2.0) <= radius_px
    if not core.any():
        return 0.0
    return round(float(np.mean(np.isfinite(image[core]))), 4)


def grid_side(fov_arcmin: float, scale_arcsec: float) -> int:
    """Pixels per side of the output grid: ceil(fov * 60 / scale)."""
    return max(1, math.ceil(fov_arcmin * 60.0 / scale_arcsec - 1e-9))


def view_wcs(ra: float, dec: float, fov_arcmin: float, scale_arcsec: float) -> tuple[WCS, int]:
    """North-up, east-left TAN WCS centred on (ra, dec), and its side in pixels.

    In FITS order (before the flip to display order) pixel y grows to the north and
    x to the west, because CDELT1 is negative.
    """
    side = grid_side(fov_arcmin, scale_arcsec)
    step = scale_arcsec / 3600.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.cdelt = [-step, step]
    wcs.wcs.crpix = [(side + 1) / 2.0, (side + 1) / 2.0]
    wcs.wcs.radesys = "ICRS"
    return wcs, side


def quality_status(fraction: float, settings: Settings) -> str:
    if fraction < settings.quality_warn_fraction:
        return "good"
    if fraction < settings.quality_bad_fraction:
        return "warn"
    return "bad"


def _to_mjy_sr(header: fits.Header, what: str) -> float:
    unit_text = str(header.get("BUNIT", "")).strip()
    try:
        unit = u.Unit(unit_text)
        return float(unit.to(MJY_SR))
    except (ValueError, u.UnitConversionError) as exc:
        raise UpstreamError(f"{what} unit {unit_text!r} is not a surface brightness") from exc


def subtract_zodi(hdul: fits.HDUList) -> np.ndarray:
    """IMAGE - ZODI in MJy/sr (float64), after checking both units."""
    image = hdul["IMAGE"]
    zodi = hdul["ZODI"]
    if image.data is None or zodi.data is None or image.data.shape != zodi.data.shape:
        raise UpstreamError("cutout IMAGE and ZODI do not match")
    k_img = _to_mjy_sr(image.header, "IMAGE")
    k_zodi = _to_mjy_sr(zodi.header, "ZODI")
    return image.data.astype(np.float64) * k_img - zodi.data.astype(np.float64) * k_zodi


def _fill_saturated(data: np.ndarray, saturated: np.ndarray) -> int:
    """Fill saturated pixels in place from the brightest finite neighbour."""
    if not saturated.any():
        return 0
    finite = np.where(np.isfinite(data), data, -np.inf)
    size = 2 * SATURATED_FILL_PX + 1
    brightest = maximum_filter(finite, size=size, mode="constant", cval=-np.inf)
    fillable = saturated & np.isfinite(brightest)
    data[fillable] = brightest[fillable]
    return int(fillable.sum())


def clean_cutout(
    hdul: fits.HDUList, ra: float, dec: float, fov_arcmin: float, settings: Settings
) -> CleanFrame:
    """Run steps 1-4 on an open cutout (IMAGE, FLAGS, ZODI extensions)."""
    try:
        data = subtract_zodi(hdul)
        flags_hdu = hdul["FLAGS"]
        flags = np.asarray(flags_hdu.data).astype(np.int64)
        wcs_in = WCS(hdul["IMAGE"].header)
    except KeyError as exc:
        raise UpstreamError("cutout lacks IMAGE, FLAGS or ZODI") from exc
    if flags.shape != data.shape:
        raise UpstreamError("cutout FLAGS and IMAGE do not match")

    bits = fl.bits_from_header(flags_hdu.header)
    bitmask = fl.mask_bits(settings.pipeline_mask_flags, bits)
    bad = fl.bad_pixels(flags, bitmask)
    data[bad] = np.nan
    saturated_px = 0
    if settings.pipeline_fill_saturated and fl.OVERFLOW in bits:
        overflow = fl.bad_pixels(flags, 1 << bits[fl.OVERFLOW])
        saturated = ~np.isfinite(data) & overflow & ~bad
        saturated_px = _fill_saturated(data, saturated)

    wcs_out, side = view_wcs(ra, dec, fov_arcmin, settings.pipeline_pixel_scale_arcsec)
    shape = (side, side)
    with np.errstate(invalid="ignore"):
        image, _ = reproject_interp((data, wcs_in), wcs_out, shape_out=shape,
                                    order="bilinear")
        flags_out, foot = reproject_interp((flags.astype(np.float64), wcs_in), wcs_out,
                                           shape_out=shape, order="nearest-neighbor")
    on_detector = (foot > 0) & np.isfinite(flags_out)
    present = 0
    if on_detector.any():
        present = int(np.bitwise_or.reduce(flags_out[on_detector].astype(np.int64)))
    names = fl.names_in(present & bitmask, bits)

    fraction = round(float(np.mean(~np.isfinite(image))), 4)
    return CleanFrame(
        image=np.flipud(image).copy(),
        flagged_fraction=fraction,
        status=quality_status(fraction, settings),
        flags=names,
        coverage=round(float(np.mean(on_detector)), 4),
        saturated_px=saturated_px,
    )
