"""Internal records for SPHEREx exposures found at IRSA.

These are not part of the API contract (that lives in ``app.models``). Routes and
providers turn them into API.md shapes in later tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MJD_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)


def mjd_to_utc_iso(mjd: float) -> str:
    """MJD (UTC) to ISO 8601 with seconds precision and a ``Z`` suffix."""
    when = MJD_EPOCH + timedelta(days=mjd)
    when = (when + timedelta(microseconds=500_000)).replace(microsecond=0)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


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
