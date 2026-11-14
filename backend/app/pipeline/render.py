"""Shared asinh stretch, 8-bit PNG output, and the stateless ``render_frame``.

- **Stretch.** ``vmin`` and ``vmax`` (MJy/sr) are the ``Settings.stretch_percentiles``
  (1st and 99.5th) of every valid pixel of every frame in the sequence, rounded to 5
  significant digits so the values written to JSON give the same PNG bytes when
  they come back as ``frame_url`` parameters. A pixel maps to
  ``x = clip((v - vmin) / (vmax - vmin), 0, 1)``, then
  ``asinh(x / a) / asinh(1 / a)`` with ``a = stretch_asinh_a`` (0.1).
- **PNG.** 8-bit greyscale, row 0 = north, column 0 = east. Valid pixels map to grey
  levels 1-255; masked, off-detector and empty pixels get ``png_fill_value`` (0).
  Pillow writes no timestamp, so equal arrays give equal bytes.
- **render_frame** finds the cutout from the epoch id alone (the cache key is built
  from the release, exposure id and band in the id), so a warm frame needs no IRSA
  call, even after a restart. On a cache miss it re-finds the exposure with
  ``resolve_epoch`` (one SIA position query) and downloads the cutout.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterable

import numpy as np
from astropy.io import fits
from PIL import Image

from app.config import Settings
from app.cutouts import (
    CutoutCache,
    QueryCache,
    cutout_key,
    default_cache,
    get_cutout,
    make_view,
    resolve_epoch,
)
from app.cutouts.download import StreamingSession
from app.errors import InvalidParameter, NotFound, UpstreamError
from app.irsa.epochs import parse_epoch_id
from app.irsa.sia import HttpSession, validate_band
from app.pipeline.clean import CleanFrame, clean_cutout

STRETCH_DIGITS = 5


def _round_sig(value: float, digits: int = STRETCH_DIGITS) -> float:
    return float(f"{value:.{digits}g}")


def compute_stretch(
    images: Iterable[np.ndarray], settings: Settings
) -> tuple[float, float]:
    """(vmin, vmax) from robust percentiles of all valid pixels, rounded.

    Falls back to (0, 1) when there is no valid pixel, and widens a flat range so
    that vmax > vmin always holds after rounding.
    """
    values = [img[np.isfinite(img)] for img in images]
    pixels = np.concatenate(values) if values else np.empty(0)
    if pixels.size == 0:
        return 0.0, 1.0
    lo_p, hi_p = settings.stretch_percentiles
    lo, hi = (float(v) for v in np.percentile(pixels, [lo_p, hi_p]))
    vmin, vmax = _round_sig(lo), _round_sig(hi)
    if not vmax > vmin:
        step = max(abs(vmin) * 1e-3, 1e-6)
        vmax = _round_sig(vmin + step)
        if not vmax > vmin:
            vmax = vmin + step
    return vmin, vmax


def to_gray8(image: np.ndarray, vmin: float, vmax: float, settings: Settings) -> np.ndarray:
    """Apply the asinh stretch: valid pixels to 1-255, others to the fill value."""
    if not (math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin):
        raise InvalidParameter("vmax must be greater than vmin")
    valid = np.isfinite(image)
    a = settings.stretch_asinh_a
    with np.errstate(invalid="ignore"):
        x = np.clip((np.where(valid, image, vmin) - vmin) / (vmax - vmin), 0.0, 1.0)
    y = np.arcsinh(x / a) / np.arcsinh(1.0 / a)
    levels = 1.0 + np.round(y * 254.0)
    out = np.where(valid, levels, settings.png_fill_value)
    return out.astype(np.uint8)


def encode_png(gray: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(gray, dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def frame_png(frame: CleanFrame, vmin: float, vmax: float, settings: Settings) -> bytes:
    return encode_png(to_gray8(frame.image, vmin, vmax, settings))


def _check_stretch(vmin: object, vmax: object) -> tuple[float, float] | None:
    if vmin is None and vmax is None:
        return None
    if vmin is None or vmax is None:
        raise InvalidParameter("pass both vmin and vmax, or neither")
    for name, value in (("vmin", vmin), ("vmax", vmax)):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidParameter(f"{name} must be a number")
        if not math.isfinite(float(value)):
            raise InvalidParameter(f"{name} must be finite")
    lo, hi = float(vmin), float(vmax)
    if not hi > lo:
        raise InvalidParameter("vmax must be greater than vmin")
    return lo, hi


def clean_path(path, ra: float, dec: float, fov: float, settings: Settings) -> CleanFrame:
    try:
        with fits.open(path, memmap=False) as hdul:
            return clean_cutout(hdul, ra, dec, fov, settings)
    except OSError as exc:
        raise UpstreamError("cached cutout could not be read") from exc


def render_frame(
    epoch_id: object,
    ra: object,
    dec: object,
    fov: object = 10.0,
    band: object = "D2",
    vmin: object = None,
    vmax: object = None,
    *,
    settings: Settings | None = None,
    cache: CutoutCache | None = None,
    session: StreamingSession | None = None,
    sia_session: HttpSession | None = None,
    query_cache: QueryCache | None = None,
) -> bytes:
    """PNG bytes of one cleaned frame; equal inputs give equal bytes.

    With ``vmin``/``vmax`` omitted, the frame gets its own stretch (API.md
    ``/frame``). Raises InvalidParameter for a bad view or stretch, NotFound for an
    unknown epoch id or one from another band, and UpstreamError or UpstreamTimeout
    when IRSA is needed and fails.
    """
    settings = settings or Settings()
    view = make_view(ra, dec, fov)
    band_s = validate_band(band)
    stretch = _check_stretch(vmin, vmax)
    if not isinstance(epoch_id, str):
        raise NotFound("unknown epoch id")
    try:
        release, obs_id, id_band = parse_epoch_id(epoch_id)
    except ValueError as exc:
        raise NotFound("unknown epoch id") from exc
    if id_band != band_s:
        raise NotFound(f"epoch {epoch_id} is not a {band_s} exposure")
    cache = cache or default_cache(settings)
    try:
        key = cutout_key(release, obs_id, band_s, view.request_ra, view.request_dec,
                         view.size_arcmin)
    except ValueError as exc:
        raise NotFound("unknown epoch id") from exc
    path = cache.lookup(key)
    if path is None:
        record = resolve_epoch(epoch_id, view.ra, view.dec, settings=settings,
                               session=sia_session, query_cache=query_cache)
        path = get_cutout(record, view.ra, view.dec, view.fov_arcmin, settings=settings,
                          cache=cache, session=session, view=view).path
    frame = clean_path(path, view.ra, view.dec, view.fov_arcmin, settings)
    lo, hi = stretch if stretch is not None else compute_stretch([frame.image], settings)
    return frame_png(frame, lo, hi, settings)
