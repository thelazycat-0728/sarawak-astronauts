"""Per-visit stacks: the mean of several cleaned frames on one grid (T-016).

- **Values.** Per pixel, the mean of the frames that hold a value there (NaN,
  masked and off-detector pixels are left out). A pixel with no valid input has
  no data (NaN, grey 0 in the PNG). ``n_valid`` counts the inputs per pixel.
- **Filled saturated cores.** They take part in the display stack ``image``, so a
  saturated star still looks like a star. ``filled`` marks every stack pixel that
  takes any part of its value from a filled pixel, and ``measurable`` sets those
  to NaN, so photometry and centroids never use a filled value.
- **Quality.** ``flagged_fraction`` is the share of stack pixels with no valid
  input; ``status`` uses the frame thresholds (``quality_status``); ``flags`` is
  the union of the inputs' masked flag names, in first-seen order.
- **Render** with a caller-given (vmin, vmax), normally the sequence's stored
  stretch, so frames and stacks share one brightness scale.

The frames of a visit sit at slightly different wavelengths, so a stack is a
broadband average over the detector array: good for detection and position, not
a colour or variability measurement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.config import Settings
from app.errors import InvalidParameter
from app.pipeline.clean import CleanFrame, core_valid_fraction, quality_status
from app.pipeline.render import encode_png, to_gray8


@dataclass(frozen=True)
class StackFrame:
    image: np.ndarray  # float64 MJy/sr, display order; NaN = no valid input
    n_valid: np.ndarray  # int, inputs with a value at each pixel
    n_frames: int
    flagged_fraction: float
    status: str
    flags: list[str]
    filled: np.ndarray | None = None  # pixels using a filled saturated value

    @property
    def measurable(self) -> np.ndarray:
        """``image`` with every pixel that uses a filled saturated value set to NaN."""
        if self.filled is None or not self.filled.any():
            return self.image
        out = self.image.copy()
        out[self.filled] = np.nan
        return out

    @property
    def core_valid(self) -> float:
        return core_valid_fraction(self.image)


def mean_valid(images: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """(per-pixel mean over the finite values, count of finite values). NaN where
    the count is 0. All images must have one shape."""
    if not images:
        raise InvalidParameter("a stack needs at least one frame")
    shape = images[0].shape
    if any(img.shape != shape for img in images):
        raise InvalidParameter("frames to stack must have the same size")
    total = np.zeros(shape, dtype=np.float64)
    count = np.zeros(shape, dtype=np.int64)
    for img in images:
        ok = np.isfinite(img)
        total[ok] += img[ok]
        count += ok
    mean = np.full(shape, np.nan, dtype=np.float64)
    has = count > 0
    mean[has] = total[has] / count[has]
    return mean, count


def stack_frames(frames: Sequence[CleanFrame], settings: Settings | None = None) -> StackFrame:
    """Mean of ``frames`` (cleaned on one grid) over their valid pixels."""
    settings = settings or Settings()
    image, count = mean_valid([f.image for f in frames])
    filled = np.zeros(image.shape, dtype=bool)
    for f in frames:
        filled |= f.filled_mask & np.isfinite(f.image)
    flags: list[str] = []
    for f in frames:
        flags += [name for name in f.flags if name not in flags]
    fraction = round(float(np.mean(count == 0)), 4)
    return StackFrame(
        image=image,
        n_valid=count,
        n_frames=len(frames),
        flagged_fraction=fraction,
        status=quality_status(fraction, settings),
        flags=flags,
        filled=filled if filled.any() else None,
    )


def stack_png(stack: StackFrame, vmin: float, vmax: float, settings: Settings) -> bytes:
    """8-bit grey PNG of the display stack with a fixed stretch (grey 0 = no data)."""
    return encode_png(to_gray8(stack.image, vmin, vmax, settings))
