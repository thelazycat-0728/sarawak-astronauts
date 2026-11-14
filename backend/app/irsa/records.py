"""Internal records for SPHEREx exposures found at IRSA.

These are not part of the API contract (that lives in ``app.models``). Routes and
providers turn them into API.md shapes in later tasks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MJD_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)


def mjd_to_utc_iso(mjd: float) -> str:
    """MJD (UTC) to ISO 8601 with seconds precision and a ``Z`` suffix."""
    when = MJD_EPOCH + timedelta(days=mjd)
    when = (when + timedelta(microseconds=500_000)).replace(microsecond=0)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_s_region(value: object) -> tuple[tuple[float, float], ...] | None:
    """Corners of an ObsCore ``s_region`` such as ``POLYGON ICRS ra1 dec1 ra2 dec2 ...``.

    Returns at least three finite (ra, dec) pairs in degrees, with a closing repeat
    of the first corner removed, or None for anything else (other shapes, frames
    other than ICRS, odd numbers of values).
    """
    if not isinstance(value, str):
        return None
    parts = value.split()
    if not parts or parts[0].upper() != "POLYGON":
        return None
    parts = parts[1:]
    if parts and not _is_number(parts[0]):
        if parts[0].upper() not in ("ICRS", "J2000"):
            return None
        parts = parts[1:]
    if len(parts) % 2 or not all(_is_number(p) for p in parts):
        return None
    numbers = [float(p) for p in parts]
    corners = [(numbers[i] % 360.0, numbers[i + 1]) for i in range(0, len(numbers), 2)]
    if len(corners) > 1 and corners[0] == corners[-1]:
        corners.pop()
    if len(corners) < 3 or any(not -90.0 <= d <= 90.0 for _, d in corners):
        return None
    return tuple(corners)


def _is_number(text: str) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


@dataclass(frozen=True)
class Exposure:
    """One SPHEREx spectral image (one exposure on one detector array)."""

    obs_id: str  # IRSA exposure id, for example "2025W34_2B_0090_4"
    band: str  # detector array, "D1".."D6"
    release: str  # display label, for example "DR1"
    collection: str  # IRSA SIA collection, for example "spherex_dr1"
    publisher_did: str  # IVOA id, "ivo://irsa.ipac/<collection>?<obs_id>/<band>"
    t_start_mjd: float  # SIA t_min (MJD, treated as UTC)
    t_end_mjd: float  # SIA t_max
    exptime_s: float | None
    # Wavelength range of the whole detector array from the SIA metadata (em_min,
    # em_max). The wavelength at the target comes from the FITS spectral WCS (T-004).
    wavelength_min_um: float | None
    wavelength_max_um: float | None
    access_url: str  # full spectral image MEF; accepts ?center=<ra>,<dec>d&size=<deg>
    # Detector footprint from the SIA ``s_region`` polygon: (ra, dec) corners in
    # degrees, without the closing repeat. None when IRSA gave none or it was unusable.
    footprint: tuple[tuple[float, float], ...] | None = None

    @property
    def t_mid_mjd(self) -> float:
        return (self.t_start_mjd + self.t_end_mjd) / 2.0

    @property
    def time_start_utc(self) -> str:
        return mjd_to_utc_iso(self.t_start_mjd)

    @property
    def time_mid_utc(self) -> str:
        return mjd_to_utc_iso(self.t_mid_mjd)


@dataclass(frozen=True)
class EpochRecord:
    """A de-duplicated exposure with its survey visit and stable epoch id."""

    id: str
    visit: int  # 1-based survey pass at this position
    exposure: Exposure


@dataclass(frozen=True)
class EpochQueryResult:
    ra: float
    dec: float
    band: str
    epochs: tuple[EpochRecord, ...]  # sorted by mid-exposure time
    duplicates_removed: int
    # Exposures returned per release before de-duplication.
    exposures_per_release: dict[str, int]

    @property
    def releases(self) -> list[str]:
        """Releases present after de-duplication, in order of first appearance."""
        seen: list[str] = []
        for epoch in self.epochs:
            if epoch.exposure.release not in seen:
                seen.append(epoch.exposure.release)
        return seen

    @property
    def visit_count(self) -> int:
        return max((e.visit for e in self.epochs), default=0)
