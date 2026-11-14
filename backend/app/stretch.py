"""The grey-level convention of every frame PNG (docs/API.md section 3, ``stretch``).

Forward (MJy/sr to grey), with ``vmin``, ``vmax`` and ``a = stretch.asinh_a``::

    x    = clip((v - vmin) / (vmax - vmin), 0, 1)
    gray = 1 + round(254 * asinh(x / a) / asinh(1 / a))     # valid pixels: 1..255
    gray = NODATA_GRAY (0)                                  # masked / off-detector / empty

Inverse (grey to MJy/sr), for ``gray`` in 1..255::

    v = vmin + (vmax - vmin) * a * sinh((gray - 1) / 254 * asinh(1 / a))

Grey 0 has no value (NaN). Values below ``vmin`` come back as ``vmin`` and values
above ``vmax`` as ``vmax``; otherwise the error is at most half a grey-level step.

Kept free of heavy imports so the mock provider can use it without loading the
cleaning pipeline (reproject).
"""

from __future__ import annotations

import math

import numpy as np

NODATA_GRAY = 0
LEVELS = 254  # valid grey levels are 1 .. 1 + LEVELS


def _check(vmin: float, vmax: float, a: float) -> None:
    if not (math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin):
        raise ValueError("vmax must be greater than vmin")
    if not (math.isfinite(a) and a > 0):
        raise ValueError("asinh_a must be a positive number")


def value_to_gray(values: np.ndarray, vmin: float, vmax: float, a: float) -> np.ndarray:
    """MJy/sr to uint8 grey; non-finite values become NODATA_GRAY."""
    _check(vmin, vmax, a)
    values = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(values)
    with np.errstate(invalid="ignore"):
        x = np.clip((np.where(valid, values, vmin) - vmin) / (vmax - vmin), 0.0, 1.0)
    y = np.arcsinh(x / a) / np.arcsinh(1.0 / a)
    levels = 1.0 + np.round(y * LEVELS)
    return np.where(valid, levels, NODATA_GRAY).astype(np.uint8)


def gray_to_value(gray: np.ndarray, vmin: float, vmax: float, a: float) -> np.ndarray:
    """Grey levels back to MJy/sr (float64); NODATA_GRAY becomes NaN."""
    _check(vmin, vmax, a)
    g = np.asarray(gray, dtype=np.float64)
    y = (g - 1.0) / LEVELS
    x = a * np.sinh(y * np.arcsinh(1.0 / a))
    return np.where(g == NODATA_GRAY, np.nan, vmin + x * (vmax - vmin))
