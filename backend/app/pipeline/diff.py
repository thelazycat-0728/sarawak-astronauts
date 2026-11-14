"""Difference images ``b - a`` of two cleaned frames (API.md ``/diff``).

- **Values.** ``d = b - a`` in MJy/sr on the shared north-up grid. A pixel with no
  data in either frame counts as no change (``d = 0``), as in the mock provider.
- **Scale.** Unless given, the 99.5th percentile of ``abs(d)`` over pixels with data
  in both frames, rounded to 5 significant digits; 1.0 if that is not positive.
- **Grey levels.** ``gray = round(128 + 127 * clip(d / scale, -1, 1))``: 128 means no
  change, white brighter in ``b``, black fainter in ``b``.
"""

from __future__ import annotations

import math

import numpy as np

from app.errors import InvalidParameter
from app.pipeline.render import STRETCH_DIGITS, encode_png

DIFF_PERCENTILE = 99.5


def _round_sig(value: float, digits: int = STRETCH_DIGITS) -> float:
    return float(f"{value:.{digits}g}")


def diff_values(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(b - a with no-data pixels set to 0, mask of pixels with data in both)."""
    if a.shape != b.shape:
        raise InvalidParameter("frames to subtract must have the same size")
    both = np.isfinite(a) & np.isfinite(b)
    d = np.zeros(a.shape, dtype=np.float64)
    d[both] = b[both] - a[both]
    return d, both


def diff_scale(a: np.ndarray, b: np.ndarray) -> float:
    d, both = diff_values(a, b)
    if not both.any():
        return 1.0
    scale = _round_sig(float(np.percentile(np.abs(d[both]), DIFF_PERCENTILE)))
    return scale if scale > 0 else 1.0


def diff_gray(a: np.ndarray, b: np.ndarray, scale: float | None = None) -> tuple[np.ndarray, float]:
    """(uint8 grey image of b - a, the scale used)."""
    if scale is None:
        scale = diff_scale(a, b)
    if not (math.isfinite(scale) and scale > 0):
        raise InvalidParameter("scale must be a positive number")
    d, _ = diff_values(a, b)
    gray = np.round(128.0 + 127.0 * np.clip(d / scale, -1.0, 1.0)).astype(np.uint8)
    return gray, scale


def diff_png(a: np.ndarray, b: np.ndarray, scale: float | None = None) -> tuple[bytes, float]:
    """(PNG bytes of b - a, the scale used)."""
    gray, used = diff_gray(a, b, scale)
    return encode_png(gray), used
