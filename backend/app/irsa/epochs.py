"""Pure functions that turn IRSA exposures into a time-lapse sequence.

Rules (documented here so the report and later tasks can quote them):

- **Sort:** by mid-exposure time, then obs_id, band and release, so ties are stable.
- **De-duplicate:** one exposure on one detector (same ``obs_id`` and ``band``)
  served by more than one release is kept once. The release listed first in the
  configured order wins (``Settings.spherex_releases``, default DR1 before QR3),
  because DR1 is the formal, validated release and QR3 a quick release.
- **Visits:** a new visit (survey pass) starts when the gap since the previous
  exposure exceeds ``VISIT_GAP_DAYS`` (45 days), or when the current visit already
  spans ``VISIT_MAX_SPAN_DAYS`` (182.6 days, half a year). Passes at one position
  are about 6 months apart and each lasts days to a few weeks, so the gap rule
  separates them; the span cap only matters near the ecliptic poles, where
  SPHEREx sees a position almost continuously. Visits are numbered from 1 at the
  position's first exposure.
- **Epoch id:** ``sx_<release>_<obs_id>_<band>``, for example
  ``sx_dr1_2025W34_2B_0090_4_D2``. It holds the release, the IRSA exposure id and
  the detector, so a later request can find the same image with no server state.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from app.irsa.records import EpochRecord, Exposure
from app.models import BAND_IDS

VISIT_GAP_DAYS = 45.0
VISIT_MAX_SPAN_DAYS = 182.6

EPOCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
_OBS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,56}$")
_PARSE_RE = re.compile(r"^sx_([a-z0-9]{1,16})_([A-Za-z0-9_-]{1,56})_(D[1-6])$")


def sort_key(exp: Exposure) -> tuple[float, str, str, str]:
    return (exp.t_mid_mjd, exp.obs_id, exp.band, exp.release)


def sort_exposures(exposures: Iterable[Exposure]) -> list[Exposure]:
    return sorted(exposures, key=sort_key)


def deduplicate(
    exposures: Iterable[Exposure], release_order: Sequence[str]
) -> tuple[list[Exposure], int]:
    """Keep one exposure per (obs_id, band). Returns (kept, number removed).

    The release that comes first in ``release_order`` wins; releases not in the
    list rank last. The kept list preserves the input order of the winners.
    """
    rank = {release: i for i, release in enumerate(release_order)}
    best: dict[tuple[str, str], Exposure] = {}
    total = 0
    for exp in exposures:
        total += 1
        key = (exp.obs_id, exp.band)
        current = best.get(key)
        if current is None or rank.get(exp.release, len(rank)) < rank.get(
            current.release, len(rank)
        ):
            best[key] = exp
    kept = list(best.values())
    return kept, total - len(kept)


def group_visits(
    exposures: Sequence[Exposure],
    gap_days: float = VISIT_GAP_DAYS,
    max_span_days: float = VISIT_MAX_SPAN_DAYS,
) -> list[int]:
    """Visit number (1-based) for each exposure. Input must be sorted by time."""
    visits: list[int] = []
    visit = 0
    visit_start = prev = None
    for exp in exposures:
        t = exp.t_mid_mjd
        if prev is not None and t < prev:
            raise ValueError("exposures must be sorted by time")
        if (
            prev is None
            or visit_start is None
            or t - prev > gap_days
            or t - visit_start >= max_span_days
        ):
            visit += 1
            visit_start = t
        visits.append(visit)
        prev = t
    return visits


def build_epoch_id(release: str, obs_id: str, band: str) -> str:
    """Stable epoch id; ValueError if the parts cannot form a safe id."""
    if not _RELEASE_RE.fullmatch(release):
        raise ValueError(f"release label not usable in an epoch id: {release!r}")
    if not _OBS_ID_RE.fullmatch(obs_id):
        raise ValueError(f"obs_id not usable in an epoch id: {obs_id!r}")
    if band not in BAND_IDS:
        raise ValueError(f"unknown band: {band!r}")
    epoch_id = f"sx_{release.lower()}_{obs_id}_{band}"
    assert EPOCH_ID_RE.fullmatch(epoch_id)
    return epoch_id


def parse_epoch_id(epoch_id: str) -> tuple[str, str, str]:
    """Inverse of build_epoch_id: (lower-case release label, obs_id, band)."""
    m = _PARSE_RE.fullmatch(epoch_id)
    if not m:
        raise ValueError(f"not a SPHEREx epoch id: {epoch_id!r}")
    return m.group(1), m.group(2), m.group(3)


def build_epochs(
    exposures: Iterable[Exposure], release_order: Sequence[str]
) -> tuple[tuple[EpochRecord, ...], int]:
    """De-duplicate, sort, group into visits and assign ids.

    Returns (epochs sorted by time, number of duplicates removed).
    """
    kept, removed = deduplicate(exposures, release_order)
    ordered = sort_exposures(kept)
    visits = group_visits(ordered)
    epochs = tuple(
        EpochRecord(
            id=build_epoch_id(exp.release, exp.obs_id, exp.band),
            visit=visit,
            exposure=exp,
        )
        for exp, visit in zip(ordered, visits, strict=True)
    )
    return epochs, removed
