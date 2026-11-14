"""Build a cleaned frame sequence in the exact ``/epochs`` response shape.

Modes:

- ``per_visit`` (live sequences): one exposure per visit, chosen from metadata
  before downloading (``select``). Presets pass ``max_per_visit`` for several.
- ``all`` (presets): every exposure IRSA lists. Failed downloads are left out and
  counted in ``Sequence.failures``.

Every frame is cleaned (``clean``), then one stretch is computed over the valid
pixels of all frames and every PNG is rendered with it. Frame URLs come from the
caller's ``frame_url(epoch_id, vmin, vmax)`` so presets can use root-relative
``/presets/...`` paths and the live API absolute URLs.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.cutouts import (
    Cutout,
    CutoutCache,
    CutoutFailure,
    default_cache,
    fetch_cutouts,
    make_view,
)
from app.cutouts.download import StreamingSession
from app.errors import InvalidParameter
from app.irsa.records import EpochQueryResult, EpochRecord
from app.irsa.sia import HttpSession, find_epochs, validate_band
from app.models import (
    BAND_RANGES_UM,
    BandInfo,
    Epoch,
    EpochsResponse,
    ImageInfo,
    Quality,
    Stretch,
    Target,
)
from app.pipeline.clean import CleanFrame, grid_side
from app.pipeline.render import clean_path, compute_stretch, frame_png
from app.pipeline.select import Attempt, select_per_visit
from app.stretch import NODATA_GRAY

Mode = Literal["per_visit", "all"]
FrameUrlFn = Callable[[str, float, float], str]
STRETCH_NOTE = ("One stretch is shared by every frame in this sequence, so brightness "
                "changes between frames are real.")


@dataclass
class Sequence:
    response: EpochsResponse
    frames: dict[str, bytes]  # epoch id -> PNG, in time order
    cleaned: dict[str, CleanFrame]
    attempts: list[Attempt] = field(default_factory=list)  # per_visit only
    skipped_visits: list[int] = field(default_factory=list)
    failures: list[CutoutFailure] = field(default_factory=list)
    downloads: int = 0
    cache_hits: int = 0
    timings_s: dict[str, float] = field(default_factory=dict)


def _wavelength(cutout: Cutout, band: str) -> float:
    """Wavelength at the target from the spectral WCS; if the target is off the
    cutout, the middle of the exposure's SIA range, else of the band."""
    if cutout.wavelength_um is not None:
        return cutout.wavelength_um
    exp = cutout.exposure
    if exp.wavelength_min_um is not None and exp.wavelength_max_um is not None:
        return round((exp.wavelength_min_um + exp.wavelength_max_um) / 2.0, 5)
    lo, hi = BAND_RANGES_UM[band]
    return round((lo + hi) / 2.0, 5)


def _check_stretch(stretch: object) -> tuple[float, float] | None:
    """A caller-given (vmin, vmax): two finite numbers with vmax > vmin."""
    if stretch is None:
        return None
    try:
        lo, hi = stretch  # type: ignore[misc]
    except (TypeError, ValueError) as exc:
        raise InvalidParameter("stretch must be a (vmin, vmax) pair") from exc
    for value in (lo, hi):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidParameter("stretch values must be numbers")
    lo, hi = float(lo), float(hi)
    if not (math.isfinite(lo) and math.isfinite(hi) and hi > lo):
        raise InvalidParameter("stretch must be finite with vmax > vmin")
    return lo, hi


def build_sequence(
    ra: object,
    dec: object,
    band: object = "D2",
    fov: object = 10.0,
    *,
    frame_url: FrameUrlFn,
    mode: Mode = "per_visit",
    name: str | None = None,
    settings: Settings | None = None,
    cache: CutoutCache | None = None,
    session: StreamingSession | None = None,
    sia_session: HttpSession | None = None,
    query: EpochQueryResult | None = None,
    max_per_visit: int = 1,
    max_candidates: int | None = None,
    keep_rejected: bool = True,
    cutout_fov: object = None,
    stretch: tuple[float, float] | None = None,
    min_core_valid: float | None = None,
) -> Sequence:
    """Query (unless ``query`` is given), fetch, clean, stretch and render.

    ``max_per_visit``, ``max_candidates`` and ``keep_rejected`` are passed to
    ``select_per_visit`` (``per_visit`` mode only; the defaults keep one exposure
    per visit). ``cutout_fov`` (arcmin, at least ``fov``) fetches the cutouts sized
    for that larger view, so one cached set of cutouts can be rendered at several
    smaller ``fov`` values; cleaning, rendering and the response use ``fov``.
    ``stretch`` = (vmin, vmax) in MJy/sr replaces the computed shared stretch (for
    example to give a smaller fov the same grey levels as a larger one).
    ``min_core_valid`` is passed to ``select_per_visit`` (``per_visit`` only).

    Raises InvalidParameter for a bad view, mode, selection limit or stretch (all
    checked before any IRSA call), and UpstreamError or UpstreamTimeout when the
    IRSA query fails. Individual cutout failures do not fail the sequence.
    """
    settings = settings or Settings()
    view = make_view(ra, dec, fov)
    fetch_view = view if cutout_fov is None else make_view(ra, dec, cutout_fov)
    if fetch_view.fov_arcmin < view.fov_arcmin:
        raise InvalidParameter("cutout_fov must be at least fov")
    band_s = validate_band(band)
    if mode not in ("per_visit", "all"):
        raise InvalidParameter("mode must be per_visit or all")
    for label, value in (("max_per_visit", max_per_visit), ("max_candidates", max_candidates)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 1):
            raise InvalidParameter(f"{label} must be a positive integer")
    stretch_pair = _check_stretch(stretch)
    cache = cache or default_cache(settings)
    timings: dict[str, float] = {}
    started = time.monotonic()

    if query is None:
        query = find_epochs(view.ra, view.dec, band_s, settings=settings,
                            session=sia_session)
        timings["query"] = time.monotonic() - started
    epochs = list(query.epochs)

    def fetch(items: list[EpochRecord]) -> tuple[Cutout | CutoutFailure, ...]:
        return fetch_cutouts(items, fetch_view.ra, fetch_view.dec, fetch_view.fov_arcmin,
                             settings=settings, cache=cache, session=session).results

    def clean(cutout: Cutout) -> CleanFrame:
        return clean_path(cutout.path, view.ra, view.dec, view.fov_arcmin, settings)

    t0 = time.monotonic()
    seq_attempts: list[Attempt] = []
    skipped: list[int] = []
    failures: list[CutoutFailure] = []
    picked: list[tuple[EpochRecord, Cutout, CleanFrame]] = []
    downloads = hits = 0
    if mode == "per_visit":
        fetched: list[Cutout] = []

        def counting_fetch(items: list[EpochRecord]) -> tuple[Cutout | CutoutFailure, ...]:
            results = fetch(items)
            fetched.extend(r for r in results if isinstance(r, Cutout))
            return results

        selection = select_per_visit(epochs, view.ra, view.dec, view.fov_arcmin,
                                     fetch=counting_fetch, clean=clean, settings=settings,
                                     max_per_visit=max_per_visit,
                                     max_candidates=max_candidates,
                                     keep_rejected=keep_rejected,
                                     min_core_valid=min_core_valid)
        picked = [(c.epoch, c.cutout, c.frame) for c in selection.chosen]
        seq_attempts, skipped = selection.attempts, selection.skipped_visits
        downloads = sum(1 for c in fetched if not c.from_cache)
        hits = sum(1 for c in fetched if c.from_cache)
        timings["fetch_and_clean"] = time.monotonic() - t0
    else:
        results = fetch(epochs)
        timings["fetch"] = time.monotonic() - t0
        t1 = time.monotonic()
        for epoch, got in zip(epochs, results, strict=True):
            if isinstance(got, CutoutFailure):
                failures.append(got)
                continue
            downloads += 0 if got.from_cache else 1
            hits += 1 if got.from_cache else 0
            picked.append((epoch, got, clean(got)))
        timings["clean"] = time.monotonic() - t1

    t2 = time.monotonic()
    if stretch_pair is None:
        vmin, vmax = compute_stretch([frame.image for _, _, frame in picked], settings)
    else:
        vmin, vmax = stretch_pair
    frames: dict[str, bytes] = {}
    out_epochs: list[Epoch] = []
    for epoch, cutout, frame in picked:
        frames[epoch.id] = frame_png(frame, vmin, vmax, settings)
        exp = epoch.exposure
        out_epochs.append(Epoch(
            id=epoch.id,
            time_utc=exp.time_mid_utc,
            mjd=round(exp.t_mid_mjd, 6),
            visit=epoch.visit,
            release=exp.release,
            obs_id=exp.obs_id,
            band=band_s,
            wavelength_um=_wavelength(cutout, band_s),
            quality=Quality(status=frame.status, flagged_fraction=frame.flagged_fraction,
                            flags=frame.flags),
            frame_url=frame_url(epoch.id, vmin, vmax),
        ))
    timings["stretch_and_render"] = time.monotonic() - t2

    present = {e.release for e in out_epochs}
    releases = [r for r in settings.spherex_releases if r in present]
    releases += sorted(present - set(releases))
    side = grid_side(view.fov_arcmin, settings.pipeline_pixel_scale_arcsec)
    response = EpochsResponse(
        synthetic=False,
        target=Target(ra=view.ra, dec=view.dec, name=name),
        fov_arcmin=view.fov_arcmin,
        band=BandInfo.for_band(band_s),
        image=ImageInfo(width_px=side, height_px=side,
                        pixel_scale_arcsec=settings.pipeline_pixel_scale_arcsec,
                        nodata_gray=NODATA_GRAY),
        stretch=Stretch(shared=True, method="asinh", vmin=vmin, vmax=vmax,
                        asinh_a=settings.stretch_asinh_a, note=STRETCH_NOTE),
        releases=releases,
        duplicates_removed=query.duplicates_removed,
        count=len(out_epochs),
        epochs=out_epochs,
        diff_url_template=None,
        diffs=[],
    )
    timings["total"] = time.monotonic() - started
    return Sequence(
        response=response,
        frames=frames,
        cleaned={epoch.id: frame for epoch, _, frame in picked},
        attempts=seq_attempts,
        skipped_visits=skipped,
        failures=failures,
        downloads=downloads,
        cache_hits=hits,
        timings_s=timings,
    )
