"""SPHEREx ``FLAGS`` bit names and the bad-pixel mask.

Bit numbers come from each cutout's FLAGS header, where ``MSKNnnnn`` holds the
name of bit ``nnnn`` (IRSA spherex_intro tutorial, section 9). Not every file
defines every bit (some DR1 files lack SNOWBALL), so the header always wins and
``DEFAULT_BITS`` is only a fallback for a header without any ``MSKN`` keyword. The
table was copied from the FLAGS headers of the DR1 and QR3 cutouts used in T-005.

``SOURCE`` ("Pixel mapped to a known source") never masks a pixel, whatever the
configuration says.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
from astropy.io import fits

SOURCE = "SOURCE"
OVERFLOW = "OVERFLOW"

DEFAULT_BITS: dict[str, int] = {
    "TRANSIENT": 0,
    "OVERFLOW": 1,
    "SUR_ERROR": 2,
    "NONFUNC": 6,
    "DICHROIC": 7,
    "MISSING_DATA": 9,
    "HOT": 10,
    "COLD": 11,
    "FULLSAMPLE": 12,
    "PHANMISS": 14,
    "NONLINEAR": 15,
    "PERSIST": 17,
    "OUTLIER": 19,
    "CROSSTALK": 20,
    "SOURCE": 21,
    "GHOST": 22,
    "GHOST_FPA": 23,
    "GHOST_EXT": 24,
    "STREAK": 25,
    "BLOOM": 26,
    "SNOWBALL": 27,
    "HALO": 28,
}


def bits_from_header(header: fits.Header) -> dict[str, int]:
    """Flag name -> bit number from ``MSKNnnnn`` keywords (DEFAULT_BITS if none)."""
    bits: dict[str, int] = {}
    for key in header:
        if not key.startswith("MSKN") or not key[4:].isdigit():
            continue
        bit = int(key[4:])
        name = str(header[key]).strip().upper()
        if name and 0 <= bit < 63:
            bits[name] = bit
    return bits or dict(DEFAULT_BITS)


def mask_bits(names: Iterable[str], bits: Mapping[str, int]) -> int:
    """Bitmask for the named flags that the file defines; SOURCE is always left out."""
    value = 0
    for name in names:
        upper = name.strip().upper()
        if upper == SOURCE or upper not in bits:
            continue
        value |= 1 << bits[upper]
    return value


def names_in(value: int, bits: Mapping[str, int]) -> list[str]:
    """Flag names whose bit is set in ``value``, sorted by bit, never SOURCE."""
    return [
        name
        for name, bit in sorted(bits.items(), key=lambda item: item[1])
        if name != SOURCE and value & (1 << bit)
    ]


def bad_pixels(flags: np.ndarray, bitmask: int) -> np.ndarray:
    """Boolean array: True where any bit of ``bitmask`` is set."""
    return (flags.astype(np.int64) & np.int64(bitmask)) != 0
