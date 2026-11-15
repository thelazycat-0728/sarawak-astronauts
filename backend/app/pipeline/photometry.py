"""Aperture photometry at a fixed sky position on one SPHEREx exposure (T-009).

**Image.** Photometry runs on the **native** detector pixels of the cached cutout
(the same file ``/epochs`` downloaded and cleaned), not on the north-up grid:
native pixels have their own ``VARIANCE``, no interpolation between neighbours
(which would correlate the noise and spread every masked pixel to its
neighbours), and the target's sub-pixel position comes from the IMAGE WCS (with
SIP). Values are ``IMAGE - ZODI`` in MJy/sr, masked exactly as the cleaning
pipeline masks them (``Settings.pipeline_mask_flags`` and every non-finite
pixel). Saturated cores are **not** filled here, so a saturated star's core is
not measurable: this is the T-015 ``measurable`` rule applied before the fill.

**Stamp.** ``extract_stamp`` keeps a square of native pixels around the target,
large enough for the biggest aperture and its annulus (``STAMP_RADIUS_ARCSEC``),
padded with NaN where it runs off the cutout. It is small (about 37 x 37 px), so
a live sequence keeps one stamp per epoch in memory.

**Measurement** (``measure``), for an aperture of radius ``r`` arcsec:

- aperture: a circle of radius ``r``, with exact fractional pixel overlap
  (photutils ``method="exact"``);
- sky: the median of the measurable pixels whose centres lie in an annulus from
  ``r + ANNULUS_GAP_ARCSEC`` to ``r + ANNULUS_GAP_ARCSEC + ANNULUS_WIDTH_ARCSEC``
  (24-48" for the default 12" aperture), subtracted from every aperture pixel;
- flux: ``sum(w * (v - sky)) * pixel_sr`` in MJy, times 1e12 for uJy, where
  ``pixel_sr`` is the solid angle of the native pixel at the target, from the
  WCS (about (6.15")^2);
- error: ``sqrt(sum(w^2 * VARIANCE) + N^2 * (pi/2) * sigma_sky^2 / n_sky)``
  in the same units, where ``N = sum(w)``, ``sigma_sky`` is the annulus scatter
  (1.4826 x MAD) and ``n_sky`` the number of sky pixels (the second term is the
  error of a median). Without a VARIANCE plane the first term uses
  ``sigma_sky^2`` per pixel instead.

No aperture correction is applied, so a point source's flux is a lower bound
(the PSF wings outside the aperture are lost).

**Quality** of a point:

- ``bad``, flux and flux_err null: any aperture pixel (any overlap) is not
  measurable (masked, NaN, a saturated core, or off the cutout), the target is
  off the cutout, or fewer than ``MIN_SKY_PX`` annulus pixels are measurable;
- ``warn``: fewer than ``THIN_ANNULUS_FRACTION`` (50 %) of the annulus pixels are
  measurable (a thin annulus), or the frame's quality is ``warn`` or ``bad``;
- ``good`` otherwise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from app.config import Settings
from app.cutouts.geometry import target_pixel
from app.errors import UpstreamError
from app.pipeline import flags as fl
from app.pipeline.clean import subtract_zodi

APERTURE_MIN_ARCSEC = 3.0
APERTURE_MAX_ARCSEC = 60.0
ANNULUS_GAP_ARCSEC = 12.0  # aperture edge to the annulus inner edge
ANNULUS_WIDTH_ARCSEC = 24.0
# Largest annulus outer radius, plus 2 px of margin in extract_stamp.
STAMP_RADIUS_ARCSEC = APERTURE_MAX_ARCSEC + ANNULUS_GAP_ARCSEC + ANNULUS_WIDTH_ARCSEC
STAMP_MARGIN_PX = 2
THIN_ANNULUS_FRACTION = 0.5
MIN_SKY_PX = 10
UJY_PER_MJY = 1e12
MJY_SR2 = (u.MJy / u.sr) ** 2
ARCSEC_PER_RAD = 180.0 / math.pi * 3600.0

METHOD = (
    "Aperture photometry at a fixed position on the native SPHEREx pixels "
    "(IMAGE - ZODI, MJy/sr) of each epoch's exposure: a circular aperture of radius "
    "aperture_arcsec with exact pixel overlap, minus the median of a sky annulus "
    "from aperture + 12\" to aperture + 36\"; flux_err from the VARIANCE plane plus "
    "the sky-median error. No aperture correction, so point-source fluxes are low. "
    "Any masked, saturated or off-frame pixel in the aperture gives a null flux "
    "and quality bad. Each epoch is at its own wavelength (wavelength_um), so part "
    "of a change between points can be colour, not time."
)


@dataclass(frozen=True)
class Stamp:
    """Native pixels around the target from one cutout."""

    image: np.ndarray  # float32 MJy/sr, IMAGE - ZODI; NaN = not measurable
    variance: np.ndarray | None  # float32 (MJy/sr)^2; None without a VARIANCE plane
    x: float  # target position, 0-based stamp pixels
    y: float
    pixel_sr: float  # solid angle of a native pixel at the target

    @property
    def pixel_scale_arcsec(self) -> float:
        return math.sqrt(self.pixel_sr) * ARCSEC_PER_RAD


@dataclass(frozen=True)
class Measurement:
    flux: float | None  # uJy
    flux_err: float | None  # uJy
    quality: str  # good, warn or bad
    sky_px: int = 0  # measurable annulus pixels
    annulus_px: int = 0  # all annulus pixels


BAD = Measurement(None, None, "bad")


def pixel_solid_angle(wcs: WCS, x: float, y: float) -> float:
    """Solid angle (sr) of the pixel at (x, y), from its four corners on the sky."""
    corners = np.array([[x - 0.5, y - 0.5], [x + 0.5, y - 0.5],
                        [x + 0.5, y + 0.5], [x - 0.5, y + 0.5]])
    ra0, dec0 = (float(v) for v in wcs.all_pix2world([[x, y]], 0)[0])
    world = wcs.all_pix2world(corners, 0)
    a0, d0 = math.radians(ra0), math.radians(dec0)
    a, d = np.radians(world[:, 0]), np.radians(world[:, 1])
    cos_c = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cos_c
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cos_c
    return float(abs(np.dot(xi, np.roll(eta, -1)) - np.dot(eta, np.roll(xi, -1))) / 2.0)


def _variance(hdul: fits.HDUList) -> np.ndarray | None:
    if "VARIANCE" not in hdul:
        return None
    hdu = hdul["VARIANCE"]
    if hdu.data is None or hdu.data.shape != hdul["IMAGE"].data.shape:
        raise UpstreamError("cutout VARIANCE and IMAGE do not match")
    unit_text = str(hdu.header.get("BUNIT", "")).strip()
    try:
        k = float(u.Unit(unit_text).to(MJY_SR2))
    except (ValueError, u.UnitConversionError) as exc:
        raise UpstreamError(f"VARIANCE unit {unit_text!r} is not (MJy/sr)^2") from exc
    return hdu.data.astype(np.float64) * k


def _cut(data: np.ndarray, x0: int, y0: int, half: int) -> np.ndarray:
    """``data[y0-half : y0+half+1, x0-half : x0+half+1]`` padded with NaN."""
    side = 2 * half + 1
    out = np.full((side, side), np.nan, dtype=np.float32)
    ny, nx = data.shape
    ys, xs = max(0, y0 - half), max(0, x0 - half)
    ye, xe = min(ny, y0 + half + 1), min(nx, x0 + half + 1)
    if ye > ys and xe > xs:
        out[ys - (y0 - half):ye - (y0 - half), xs - (x0 - half):xe - (x0 - half)] = \
            data[ys:ye, xs:xe]
    return out


def extract_stamp(hdul: fits.HDUList, ra: float, dec: float,
                  settings: Settings) -> Stamp | None:
    """The target's stamp from an open cutout, or None when the target is off it.

    Raises UpstreamError for a cutout without IMAGE, FLAGS or ZODI, or with
    units that are not surface brightness."""
    try:
        data = subtract_zodi(hdul)
        flags_hdu = hdul["FLAGS"]
        wcs = WCS(hdul["IMAGE"].header)
    except KeyError as exc:
        raise UpstreamError("cutout lacks IMAGE, FLAGS or ZODI") from exc
    flags = np.asarray(flags_hdu.data).astype(np.int64)
    if flags.shape != data.shape:
        raise UpstreamError("cutout FLAGS and IMAGE do not match")
    bits = fl.bits_from_header(flags_hdu.header)
    bad = fl.bad_pixels(flags, fl.mask_bits(settings.pipeline_mask_flags, bits))
    variance = _variance(hdul)
    measurable = ~bad & np.isfinite(data)
    if variance is not None:
        measurable &= np.isfinite(variance) & (variance >= 0)
    data[~measurable] = np.nan  # no saturated fill: a core is not measurable

    pixel = target_pixel(hdul, ra, dec, wcs)
    if pixel is None:
        return None
    x, y = pixel
    pixel_sr = pixel_solid_angle(wcs, x, y)
    scale = math.sqrt(pixel_sr) * ARCSEC_PER_RAD
    half = math.ceil(STAMP_RADIUS_ARCSEC / scale) + STAMP_MARGIN_PX
    x0, y0 = round(x), round(y)
    var_stamp = None
    if variance is not None:
        variance[~measurable] = np.nan
        var_stamp = _cut(variance, x0, y0, half)
    return Stamp(image=_cut(data, x0, y0, half), variance=var_stamp,
                 x=x - x0 + half, y=y - y0 + half, pixel_sr=pixel_sr)


def annulus_radii(aperture_arcsec: float) -> tuple[float, float]:
    """(inner, outer) sky annulus radii in arcsec for an aperture radius."""
    inner = aperture_arcsec + ANNULUS_GAP_ARCSEC
    return inner, inner + ANNULUS_WIDTH_ARCSEC


def measure(stamp: Stamp | None, aperture_arcsec: float,
            frame_status: str = "good") -> Measurement:
    """Flux (uJy) in a circular aperture at the stamp's target (module rules)."""
    from photutils.aperture import CircularAnnulus, CircularAperture

    if not APERTURE_MIN_ARCSEC <= aperture_arcsec <= APERTURE_MAX_ARCSEC:
        raise ValueError("aperture must be between 3 and 60 arcsec")
    if stamp is None:
        return BAD
    scale = stamp.pixel_scale_arcsec
    shape = stamp.image.shape
    centre = (stamp.x, stamp.y)
    weights = CircularAperture(centre, aperture_arcsec / scale).to_mask(
        method="exact").to_image(shape)
    r_in, r_out = annulus_radii(aperture_arcsec)
    ring = CircularAnnulus(centre, r_in / scale, r_out / scale).to_mask(
        method="center").to_image(shape)
    if weights is None or ring is None:
        return BAD
    in_ap = weights > 0
    ring_px = ring > 0
    image = stamp.image.astype(np.float64)
    ok = np.isfinite(image)
    sky_values = image[ring_px & ok]
    annulus_px, sky_px = int(ring_px.sum()), int(sky_values.size)
    if not ok[in_ap].all() or sky_px < MIN_SKY_PX:
        return Measurement(None, None, "bad", sky_px, annulus_px)
    sky = float(np.median(sky_values))
    sigma_sky = 1.4826 * float(np.median(np.abs(sky_values - sky)))
    w = weights[in_ap]
    n_ap = float(w.sum())
    flux_sb = float(np.sum(w * (image[in_ap] - sky)))
    if stamp.variance is not None:
        pixel_var = float(np.sum(w**2 * stamp.variance[in_ap].astype(np.float64)))
    else:
        pixel_var = float(np.sum(w**2)) * sigma_sky**2
    sky_var = n_ap**2 * (math.pi / 2.0) * sigma_sky**2 / sky_px
    to_ujy = stamp.pixel_sr * UJY_PER_MJY
    thin = sky_px < THIN_ANNULUS_FRACTION * annulus_px
    quality = "warn" if thin or frame_status in ("warn", "bad") else "good"
    return Measurement(
        flux=round(flux_sb * to_ujy, 1),
        flux_err=round(math.sqrt(pixel_var + sky_var) * to_ujy, 1),
        quality=quality,
        sky_px=sky_px,
        annulus_px=annulus_px,
    )
